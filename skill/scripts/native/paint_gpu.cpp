#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi.h>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <vector>
#include <algorithm>
#include <chrono>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "d3dcompiler.lib")

#define EXPORT extern "C" __declspec(dllexport)

static const char* kHLSL = R"HLSL(
struct Params {
    float4 color;
    uint W, H;
    int use_region, region_id;
    uint nstamps, th_splat;
    uint stamp_base; float wet;
    uint th_over, pad0;
    float opacity;
    int bx0, by0, bx1, by1;
    int use_tip, tip_base, tip_w, tip_h;
    int lut_slot, pad2;
    int blend_mode, pad3;
    int branch, cosn;
    float kw, tw, tc, thr;
    float color_dyn;
    uint use_color;
    uint seed; float cpad1;
};
cbuffer P : register(b0) { Params p; };
StructuredBuffer<float> lutbuf : register(t3);
StructuredBuffer<float4> srcLayer : register(t4);
StructuredBuffer<float4> snap : register(t5);
RWStructuredBuffer<float4> accbuf : register(u2);

StructuredBuffer<float4> stamps : register(t0);   // 每个 stamp 两个 float4：pos/R/a, dir/-
StructuredBuffer<int> region : register(t1);
StructuredBuffer<float> tips : register(t2);
RWStructuredBuffer<uint> scratch : register(u0);
RWStructuredBuffer<float4> layer : register(u1);

inline void splat_key(uint idx, float a, uint k) {
    if (p.use_color) {
        uint a16 = (uint)(saturate(a) * 65535.0f + 0.5f);
        InterlockedMax(scratch[idx], (a16 << 16) | (65535u - min(k, 65535u)));
    } else {
        InterlockedMax(scratch[idx], asuint(a));
    }
}

[numthreads(256,1,1)]
void cs_splat(uint3 dt : SV_DispatchThreadID) {
    uint i_end = p.stamp_base + p.nstamps;
    for (uint i = p.stamp_base + dt.x; i < i_end; i += p.th_splat) {
        float4 s = stamps[i * 2];
        float4 dir = stamps[i * 2 + 1];
        int R = (int)(s.z + 0.5f);
        float inv = 1.0f / max(1e-6f, s.z * s.z);
        float ext = p.use_tip ? 1.3f : 1.0f;
        int x0 = max(0, (int)floor(s.x - s.z * ext)), x1 = min((int)p.W - 1, (int)ceil(s.x + s.z * ext));
        int y0 = max(0, (int)floor(s.y - s.z * ext)), y1 = min((int)p.H - 1, (int)ceil(s.y + s.z * ext));
        if (p.use_tip) {
            float tipInv = 1.0f / max(1e-6f, s.z * 2.0f);
            int tw1 = p.tip_w - 1, th1 = p.tip_h - 1;
            for (int y = y0; y <= y1; ++y) {
                float dy = (float)y + 0.5f - s.y;
                for (int x = x0; x <= x1; ++x) {
                    float dx = (float)x + 0.5f - s.x;
                    // CPU 的笔尖只画半径 1.3R 的圆内（paint_core.cpp stamp_band 的 R2 = R*R*1.69）；
                    // 这里少了这个裁剪，方框四角会多出一圈像素 —— 位图笔尖 0.2 的 mean diff 就来自它。
                    if (dx * dx + dy * dy > s.z * s.z * 1.69f) continue;
                    float u = (dx * dir.x + dy * dir.y) * tipInv + 0.5f;
                    float v = (-dx * dir.y + dy * dir.x) * tipInv + 0.5f;
                    if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) continue;
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != p.region_id && rv != -2) continue; }
                    int ix = (int)(u * tw1 + 0.5f);
                    int iy = (int)(v * th1 + 0.5f);
                    float a = tips[p.tip_base + iy * p.tip_w + ix] * s.w;
                    if (a > 0.0f) splat_key(idx, a, i - p.stamp_base);
                }
            }
        } else if (p.branch == 1) {
            // 平笔 / 鬃毛：椭圆 + 条纹 grain
            for (int y = y0; y <= y1; ++y) {
                float dy = (float)y + 0.5f - s.y;
                for (int x = x0; x <= x1; ++x) {
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != p.region_id && rv != -2) continue; }
                    float dx = (float)x + 0.5f - s.x;
                    float along = dx * dir.x + dy * dir.y;
                    float perp = -dx * dir.y + dy * dir.x;
                    float aa = along / (s.z * 1.25f), bb = perp / (s.z * p.kw);
                    float e2 = aa * aa + bb * bb;
                    if (e2 >= 1.0f) continue;
                    float base;
                    if (e2 <= 0.49f) base = 1.0f;
                    else { float e = sqrt(e2); base = 1.0f - smoothstep(0.70f, 1.0f, e); }
                    float gi_f = (perp / s.z + 0.62f) * (2048.0f / 1.24f);
                    int gi = (int)gi_f;
                    if (gi < 0) gi = 0;
                    if (gi > 2047) gi = 2047;
                    float fr = gi_f - (float)gi;
                    if (fr < 0.0f) fr = 0.0f;
                    if (fr > 1.0f) fr = 1.0f;
                    float t0 = -0.62f + 1.24f * (float)gi / 2048.0f;
                    float t1 = -0.62f + 1.24f * (float)(gi + 1) / 2048.0f;
                    float g0 = cos(t0 * (float)p.cosn), g1 = cos(t1 * (float)p.cosn);
                    float gro = g0 + (g1 - g0) * fr;
                    float a = base * (p.tw + p.tc * gro) * s.w;
                    if (a > 0.0f) splat_key(idx, a, i - p.stamp_base);
                }
            }
        } else if (p.branch == 2) {
            // 干笔 / 粉笔：hash 噪声阈值
            for (int y = y0; y <= y1; ++y) {
                for (int x = x0; x <= x1; ++x) {
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != p.region_id && rv != -2) continue; }
                    float dx = (float)x + 0.5f - s.x, dy = (float)y + 0.5f - s.y;
                    float d = sqrt(dx * dx + dy * dy);
                    if (d > s.z * 1.3f) continue;
                    uint xh = (uint)x, yh = (uint)y, sh = 0u;
                    uint hh = xh * 374761393u + yh * 668265263u + sh * 2246822519u;
                    hh = (hh ^ (hh >> 13u)) * 1274126177u;
                    hh ^= hh >> 16u;
                    float rnd = (float)(hh & 0xFFFFFFu) / 16777216.0f;
                    int k = (int)min(255.0f, d / s.z * 255.0f);
                    float a = rnd > p.thr ? lutbuf[p.lut_slot * 256 + k] * s.w : 0.0f;
                    if (a > 0.0f) splat_key(idx, a, i - p.stamp_base);
                }
            }
        } else {
            for (int y = y0; y <= y1; ++y) {
                for (int x = x0; x <= x1; ++x) {
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != p.region_id && rv != -2) continue; }
                    float dx = (float)x + 0.5f - s.x, dy = (float)y + 0.5f - s.y;
                    float e2 = (dx * dx + dy * dy) * inv;
                    int k = (int)(e2 * 255.0f);
                    if (k > 255) k = 255;
                    float a = lutbuf[p.lut_slot * 256 + k] * s.w;
                    if (a > 0.0f) splat_key(idx, a, i - p.stamp_base);
                }
            }
        }
    }
}

void rgb2hsl_c(float3 c, out float h, out float s, out float l) {
    float mx = max(c.r, max(c.g, c.b)), mn = min(c.r, min(c.g, c.b));
    l = (mx + mn) * 0.5f;
    float dd = mx - mn;
    if (dd < 1e-6f) { h = 0.0f; s = 0.0f; return; }
    s = l > 0.5f ? dd / (2.0f - mx - mn) : dd / (mx + mn);
    if (mx == c.r) h = (c.g - c.b) / dd + (c.g < c.b ? 6.0f : 0.0f);
    else if (mx == c.g) h = (c.b - c.r) / dd + 2.0f;
    else h = (c.r - c.g) / dd + 4.0f;
    h /= 6.0f;
}
float hue2rgb_c(float p, float q, float t) {
    if (t < 0.0f) t += 1.0f;
    if (t > 1.0f) t -= 1.0f;
    if (t < 1.0f / 6.0f) return p + (q - p) * 6.0f * t;
    if (t < 0.5f) return q;
    if (t < 2.0f / 3.0f) return p + (q - p) * (2.0f / 3.0f - t) * 6.0f;
    return p;
}
float3 hsl2rgb_c(float h, float s, float l) {
    if (s < 1e-6f) return float3(l, l, l);
    float q = l < 0.5f ? l * (1.0f + s) : l + s - l * s;
    float p = 2.0f * l - q;
    return float3(hue2rgb_c(p, q, h + 1.0f / 3.0f), hue2rgb_c(p, q, h), hue2rgb_c(p, q, h - 1.0f / 3.0f));
}
float hash_c(uint x, uint y, uint s) {
    uint h = x * 374761393u + y * 668265263u + s * 2246822519u;
    h = (h ^ (h >> 13)) * 1274126177u;
    h ^= h >> 16;
    return (float)(h & 0xFFFFFFu) / 16777216.0f;
}
float cb_c(float s, float d) {           // 与 CPU 的 cb lambda 同式
    if (d <= 0.0f) return 0.0f;
    if (s >= 1.0f) return d;
    return 1.0f - min(1.0f, (1.0f - d) / s);
}
float hl_c(float s, float d) {           // hardlight
    return s <= 0.5f ? 2.0f * s * d : 1.0f - 2.0f * (1.0f - s) * (1.0f - d);
}
float vl_c(float s, float d) {           // vividlight
    if (s <= 0.5f) {
        float t = 1.0f - 2.0f * s;
        return t >= 1.0f ? 0.0f : max(0.0f, 1.0f - (1.0f - d) / t);
    }
    float t = 2.0f * s - 1.0f;
    return min(1.0f, d / max(1e-6f, 1.0f - t));
}
float3 blend3(float3 s, float3 d, int mode) {
    float3 r = s;
    if (mode == 1) r = s * d;
    else if (mode == 2) r = s + d - s * d;
    else if (mode == 3) {
        r.r = d.r < 0.5f ? 2.0f * s.r * d.r : 1.0f - 2.0f * (1.0f - s.r) * (1.0f - d.r);
        r.g = d.g < 0.5f ? 2.0f * s.g * d.g : 1.0f - 2.0f * (1.0f - s.g) * (1.0f - d.g);
        r.b = d.b < 0.5f ? 2.0f * s.b * d.b : 1.0f - 2.0f * (1.0f - s.b) * (1.0f - d.b);
    } else if (mode == 4) {
        for (int c = 0; c < 3; ++c) {
            float sc = s[c], dc = d[c];
            if (sc <= 0.5f) r[c] = dc - (1.0f - 2.0f * sc) * dc * (1.0f - dc);
            else {
                float dd = dc <= 0.25f ? ((16.0f * dc - 12.0f) * dc + 4.0f) * dc : sqrt(dc);
                r[c] = dc + (2.0f * sc - 1.0f) * (dd - dc);
            }
        }
    }
    // 编号必须与 CPU 的 enum Blend / paint_native.BLENDS 一一对应（5=dodge, 7=add, 9=lighten,
    // 10=colorburn, 11=hardlight, 12=vividlight）。此前 GPU 侧是按"实现顺序"排的，整体错位了 2 位。
    else if (mode == 5) r = float3(d.r >= 1.0f ? 1.0f : saturate(s.r / (1.0f - d.r)),
                                   d.g >= 1.0f ? 1.0f : saturate(s.g / (1.0f - d.g)),
                                   d.b >= 1.0f ? 1.0f : saturate(s.b / (1.0f - d.b)));  // dodge
    else if (mode == 6) r = d - s;
    else if (mode == 7) r = s + d;
    else if (mode == 8) r = min(s, d);
    else if (mode == 9) r = max(s, d);
    else if (mode == 10) r = float3(cb_c(s.r, d.r), cb_c(s.g, d.g), cb_c(s.b, d.b));   // colorburn
    else if (mode == 11) r = float3(hl_c(s.r, d.r), hl_c(s.g, d.g), hl_c(s.b, d.b));   // hardlight
    else if (mode == 12) r = float3(vl_c(s.r, d.r), vl_c(s.g, d.g), vl_c(s.b, d.b));   // vividlight
    else if (mode == 13) r = d + 2.0f * s - 1.0f;
    else if (mode == 14) {   // pinlight
        r.r = s.r <= 0.5f ? min(d.r, 2.0f * s.r) : max(d.r, 2.0f * s.r - 1.0f);
        r.g = s.g <= 0.5f ? min(d.g, 2.0f * s.g) : max(d.g, 2.0f * s.g - 1.0f);
        r.b = s.b <= 0.5f ? min(d.b, 2.0f * s.b) : max(d.b, 2.0f * s.b - 1.0f);
    }
    else if (mode == 15) r = abs(s - d);
    else if (mode == 16) r = s + d - 2.0f * s * d;
    else if (mode == 17) r = float3(d.r <= 0.0f ? 1.0f : saturate(s.r / d.r),
                                    d.g <= 0.0f ? 1.0f : saturate(s.g / d.g),
                                    d.b <= 0.0f ? 1.0f : saturate(s.b / d.b));
    else if (mode == 18) r = s + d - 1.0f;
    else if (mode == 19) r = saturate(d - s + 0.5f);
    else if (mode == 20) r = saturate(d + s - 0.5f);
    else if (mode == 21) r = float3(s.r + d.r >= 1.0f ? 1.0f : 0.0f,
                                    s.g + d.g >= 1.0f ? 1.0f : 0.0f,
                                    s.b + d.b >= 1.0f ? 1.0f : 0.0f);
    else if (mode >= 22) {   // hue / saturation / color / luminosity（HSL，与 CPU 同式）
        float sh, ss, sl, dh, ds, dl;
        rgb2hsl_c(s, sh, ss, sl);
        rgb2hsl_c(d, dh, ds, dl);
        float hh = dh, s2 = ds, l2 = dl;
        if (mode == 22) { hh = sh; s2 = ds; l2 = dl; }
        else if (mode == 23) { hh = dh; s2 = ss; l2 = dl; }
        else if (mode == 24) { hh = sh; s2 = ss; l2 = dl; }
        else { hh = dh; s2 = ds; l2 = sl; }
        r = hsl2rgb_c(hh, s2, l2);
    }
    return saturate(r);
}

[numthreads(256,1,1)]
void cs_composite(uint3 dt : SV_DispatchThreadID) {
    uint n = p.W * p.H;
    for (uint i = dt.x; i < n; i += p.th_over) {
        float4 s = srcLayer[i];
        if (s.a <= 0.0f) continue;
        float4 d = accbuf[i];
        float isa = 1.0f / s.a;
        float3 sr = s.rgb * isa;
        float3 dr = d.a > 0.0f ? d.rgb / d.a : float3(0.0f, 0.0f, 0.0f);
        float3 br = blend3(sr, dr, p.blend_mode);
        float ao = s.a + d.a * (1.0f - s.a);
        if (ao <= 1e-6f) { accbuf[i] = float4(0,0,0,0); continue; }
        float wa = d.a * (1.0f - s.a);
        accbuf[i] = float4(br * s.a + dr * wa, ao);
    }
}

RWStructuredBuffer<uint> acc8 : register(u3);
[numthreads(256,1,1)]
void cs_final(uint3 dt : SV_DispatchThreadID) {
    uint n = p.W * p.H;
    for (uint i = dt.x; i < n; i += p.th_over) {
        float4 a = accbuf[i];
        if (a.a <= 0.0f) { acc8[i] = 0u; continue; }
        float inv = 1.0f / a.a;
        uint r = (uint)min(255.0f, max(0.0f, a.r * inv * 255.0f + 0.5f));
        uint g = (uint)min(255.0f, max(0.0f, a.g * inv * 255.0f + 0.5f));
        uint b = (uint)min(255.0f, max(0.0f, a.b * inv * 255.0f + 0.5f));
        uint al = (uint)min(255.0f, max(0.0f, a.a * 255.0f + 0.5f));
        acc8[i] = r | (g << 8) | (b << 16) | (al << 24);
    }
}

[numthreads(256,1,1)]
void cs_over(uint3 dt : SV_DispatchThreadID) {
    int bw = p.bx1 - p.bx0 + 1, bh = p.by1 - p.by0 + 1;
    uint n = (uint)(bw * bh);
    for (uint t = dt.x; t < n; t += p.th_over) {
        int x = p.bx0 + (int)(t % (uint)bw);
        int y = p.by0 + (int)(t / (uint)bw);
        uint idx = (uint)y * p.W + (uint)x;
        uint bits = scratch[idx];
        if (bits == 0u) continue;
        float raw_a = p.use_color ? ((float)(bits >> 16) / 65535.0f) : asfloat(bits);
        float sa = raw_a * p.opacity;
        if (sa > 0.0f) {
            bool ok = true;
            if (p.use_region) { int rv = region[idx]; ok = (rv == p.region_id || rv == -2); }
            if (ok) {
                float3 col = p.color.rgb;
                if (p.use_color) {
                    uint k = 65535u - (bits & 0xFFFFu);      // 该像素获胜 stamp 在本笔内的序号
                    float h16 = hash_c(p.seed, k, 16u);
                    float h17 = hash_c(p.seed, k, 17u);
                    float m = 1.0f + p.color_dyn * (h16 * 2.0f - 1.0f);
                    float twk = p.color_dyn * 0.25f * (h17 * 2.0f - 1.0f);
                    col = saturate(float3(col.r * m * (1.0f + twk), col.g * m, col.b * m * (1.0f - twk)));
                }
                if (p.wet > 0.0f) {
                    float4 s = snap[idx];
                    if (s.a > 1e-6f) col = lerp(p.color.rgb, s.rgb / s.a, p.wet);
                }
                float4 d = layer[idx];
                float inv2 = 1.0f - sa;
                layer[idx] = float4(col * sa + d.rgb * inv2, sa + d.a * inv2);
            }
        }
        scratch[idx] = 0u;
    }
}
)HLSL";

enum { P_ROUND_SOFT = 0, P_ROUND_HARD, P_FLAT, P_DRY, P_AIRBRUSH, P_LINER, P_CHALK, P_SPONGE, P_BRISTLE, P_MARKER };

static inline float smoothstep_(float e0, float e1, float x) {
    float t = (x - e0) / (e1 - e0);
    t = t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t);
    return t * t * (3.0f - 2.0f * t);
}

static void build_tip_table(float* tbl, float hardness, int preset) {
    for (int k = 0; k < 256; ++k) {
        float t = (float)k / 255.0f;
        float d = sqrtf(t);
        float v;
        switch (preset) {
            case P_ROUND_HARD: v = d <= 0.94f ? 1.0f : 1.0f - smoothstep_(0.94f, 1.0f, d); break;
            case P_AIRBRUSH: { float b = 1.0f - smoothstep_(0.15f, 1.0f, d); v = b * b * 0.5f; break; }
            case P_LINER: v = d <= 0.975f ? 1.0f : 1.0f - smoothstep_(0.975f, 1.0f, d); break;
            case P_CHALK: v = 1.0f - smoothstep_(0.30f, 1.0f, d); break;
            case P_SPONGE: v = d <= 0.80f ? 1.0f : 1.0f - smoothstep_(0.80f, 1.0f, d); break;
            case P_BRISTLE: v = 1.0f - smoothstep_(0.45f, 1.0f, d); break;
            case P_MARKER: v = d <= 0.90f ? 0.98f : 0.98f * (1.0f - smoothstep_(0.90f, 1.0f, d)); break;
            case P_DRY: v = 1.0f - smoothstep_(hardness, 1.0f, d); break;
            default: {
                float core = hardness;
                v = d <= core ? 1.0f : 1.0f - smoothstep_(core, 1.0f, d);
            }
        }
        tbl[k] = v;
    }
}

struct Params {
    float color[4];
    unsigned int W, H;
    int use_region, region_id;
    unsigned int nstamps, th_splat;
    unsigned int stamp_base; float wet;
    unsigned int th_over, pad0;
    float opacity;
    int bx0, by0, bx1, by1;
    int use_tip, tip_base, tip_w, tip_h;
    int lut_slot, pad2;
    int blend_mode, pad3;
    int branch, cosn;
    float kw, tw, tc, thr;
    float color_dyn;
    unsigned int use_color;
    uint32_t seed; float cpad1;
    unsigned int bs, bo;   // host only
};

struct Layer { ID3D11Buffer* buf; ID3D11UnorderedAccessView* uav; ID3D11ShaderResourceView* srv; };

struct Tip { int base, w, h; };

struct Gpu {
    int W = 0, H = 0;
    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    ID3D11ComputeShader* cs_splat = nullptr;
    ID3D11ComputeShader* cs_over = nullptr;
    ID3D11ComputeShader* cs_comp = nullptr;
    ID3D11Buffer* acc = nullptr;
    ID3D11UnorderedAccessView* accUav = nullptr;
    ID3D11Buffer* base = nullptr;
    ID3D11Buffer* acc8 = nullptr;
    ID3D11UnorderedAccessView* acc8Uav = nullptr;
    ID3D11ComputeShader* cs_final = nullptr;
    ID3D11Buffer* snap = nullptr;
    ID3D11ShaderResourceView* snapSrv = nullptr;
    float wet = 0.0f;
    // 每实例动态参数（此前是文件级 static，多个 painter 会互相覆盖）
    float color_dyn = 0.0f;
    float dyn_scatter = 0.0f, dyn_size = 0.0f, dyn_alpha = 0.0f, dyn_angle = 0.0f, dyn_taper = 0.0f;
    ID3D11Buffer* scratch = nullptr;
    ID3D11UnorderedAccessView* scratchUav = nullptr;
    ID3D11Buffer* stamps = nullptr;
    ID3D11ShaderResourceView* stampsSrv = nullptr;
    ID3D11Buffer* regionBuf = nullptr;
    ID3D11ShaderResourceView* regionSrv = nullptr;
    ID3D11Buffer* dummy = nullptr;
    ID3D11ShaderResourceView* dummySrv = nullptr;
    ID3D11Buffer* cb = nullptr;
    ID3D11Buffer* lutb = nullptr;
    ID3D11ShaderResourceView* lutSrv = nullptr;
    int lut_preset[64]; float lut_hard[64]; int lut_n = 0;
    ID3D11Buffer* tipbuf = nullptr;
    ID3D11ShaderResourceView* tipSrv = nullptr;
    std::vector<float> tipdata;
    std::vector<Tip> tips;
    std::vector<Layer> layers;
    bool hasRegion = false;
};

static double g_t_expand = 0, g_t_upload = 0, g_t_splat = 0, g_t_over = 0;
static inline double now_ms() {
    return std::chrono::duration<double, std::milli>(std::chrono::high_resolution_clock::now().time_since_epoch()).count();
}

static ID3D11ComputeShader* compile_cs(ID3D11Device* dev, const char* src, const char* entry) {
    ID3DBlob* bc = nullptr; ID3DBlob* err = nullptr;
    HRESULT hr = D3DCompile(src, strlen(src), nullptr, nullptr, nullptr, entry, "cs_5_0", 0, 0, &bc, &err);
    if (FAILED(hr)) {
        printf("[gpu] compile %s failed: %s\n", entry, err ? (char*)err->GetBufferPointer() : "?");
        return nullptr;
    }
    ID3D11ComputeShader* sh = nullptr;
    dev->CreateComputeShader(bc->GetBufferPointer(), bc->GetBufferSize(), nullptr, &sh);
    bc->Release();
    return sh;
}

static ID3D11Buffer* make_buf(ID3D11Device* dev, UINT bytes, UINT stride, UINT bind, UINT misc, UINT cpu) {
    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = bytes;
    bd.Usage = (cpu == D3D11_CPU_ACCESS_READ) ? D3D11_USAGE_STAGING : D3D11_USAGE_DEFAULT;
    bd.BindFlags = bind;
    bd.MiscFlags = misc;
    bd.StructureByteStride = stride;
    bd.CPUAccessFlags = cpu;
    ID3D11Buffer* b = nullptr;
    dev->CreateBuffer(&bd, nullptr, &b);
    return b;
}

EXPORT void* pg_create(int w, int h) {
    Gpu* g = new Gpu();
    g->W = w; g->H = h;
    D3D_FEATURE_LEVEL fl;
    if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                 D3D11_SDK_VERSION, &g->dev, &fl, &g->ctx))) {
        delete g;
        return nullptr;
    }
    g->cs_splat = compile_cs(g->dev, kHLSL, "cs_splat");
    g->cs_over = compile_cs(g->dev, kHLSL, "cs_over");
    g->cs_comp = compile_cs(g->dev, kHLSL, "cs_composite");
    g->cs_final = compile_cs(g->dev, kHLSL, "cs_final");
    if (!g->cs_splat || !g->cs_over || !g->cs_comp || !g->cs_final) { delete g; return nullptr; }
    {
        static std::vector<float> az;
        if (az.size() < (size_t)w * h * 4) az.assign((size_t)w * h * 4, 0.0f);
        D3D11_BUFFER_DESC bd{};
        bd.ByteWidth = (UINT)w * (UINT)h * 16;
        bd.Usage = D3D11_USAGE_DEFAULT;
        bd.BindFlags = D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE;
        bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
        bd.StructureByteStride = 16;
        D3D11_SUBRESOURCE_DATA init{};
        init.pSysMem = az.data();
        g->dev->CreateBuffer(&bd, &init, &g->acc);
    }
    {
        D3D11_UNORDERED_ACCESS_VIEW_DESC ud{};
        ud.Format = DXGI_FORMAT_UNKNOWN;
        ud.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
        ud.Buffer.NumElements = (UINT)w * (UINT)h;
        g->dev->CreateUnorderedAccessView(g->acc, &ud, &g->accUav);
        UINT npix2 = (UINT)w * (UINT)h;
        g->base = make_buf(g->dev, npix2 * 16, 16, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
        g->acc8 = make_buf(g->dev, npix2 * 4, 4, D3D11_BIND_UNORDERED_ACCESS, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
        g->snap = make_buf(g->dev, npix2 * 16, 16, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
        if (g->snap) {
            D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
            sd.Format = DXGI_FORMAT_UNKNOWN;
            sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
            sd.Buffer.FirstElement = 0;
            sd.Buffer.NumElements = npix2;
            g->dev->CreateShaderResourceView(g->snap, &sd, &g->snapSrv);
        }
        D3D11_UNORDERED_ACCESS_VIEW_DESC u8{};
        u8.Format = DXGI_FORMAT_UNKNOWN;
        u8.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
        u8.Buffer.NumElements = npix2;
        g->dev->CreateUnorderedAccessView(g->acc8, &u8, &g->acc8Uav);
    }
    UINT npix = (UINT)w * (UINT)h;
    g->scratch = make_buf(g->dev, npix * 4, 4, D3D11_BIND_UNORDERED_ACCESS, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    D3D11_UNORDERED_ACCESS_VIEW_DESC ud{};
    ud.Format = DXGI_FORMAT_UNKNOWN;
    ud.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    ud.Buffer.NumElements = npix;
    g->dev->CreateUnorderedAccessView(g->scratch, &ud, &g->scratchUav);
    g->dummy = make_buf(g->dev, 16, 16, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
    sd.Format = DXGI_FORMAT_UNKNOWN;
    sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    sd.Buffer.NumElements = 1;
    g->dev->CreateShaderResourceView(g->dummy, &sd, &g->dummySrv);
    g->cb = make_buf(g->dev, 256, 0, D3D11_BIND_CONSTANT_BUFFER, 0, 0);
    g->lutb = make_buf(g->dev, 32 * 256 * 4, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    {
        D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
        sd.Format = DXGI_FORMAT_UNKNOWN;
        sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        sd.Buffer.NumElements = 32 * 256;
        g->dev->CreateShaderResourceView(g->lutb, &sd, &g->lutSrv);
    }
    for (int i = 0; i < 64; ++i) { g->lut_preset[i] = -1; g->lut_hard[i] = -1.0f; }
    return g;
}

EXPORT void pg_destroy(void* p) {
    Gpu* g = (Gpu*)p;
    if (!g) return;
    for (auto& l : g->layers) { if (l.srv) l.srv->Release(); if (l.uav) l.uav->Release(); if (l.buf) l.buf->Release(); }
    if (g->accUav) g->accUav->Release();
    if (g->acc8Uav) g->acc8Uav->Release();
    if (g->snapSrv) g->snapSrv->Release();
    if (g->snap) g->snap->Release();
    for (auto* b : { g->acc, g->base, g->acc8 }) if (b) b->Release();
    if (g->scratchUav) g->scratchUav->Release();
    for (auto* v : { g->stampsSrv, g->regionSrv, g->dummySrv, g->tipSrv, g->lutSrv })
        if (v) v->Release();
    for (auto* b : { g->scratch, g->stamps, g->regionBuf, g->dummy, g->cb, g->lutb, g->tipbuf })
        if (b) b->Release();
    if (g->cs_splat) g->cs_splat->Release();
    if (g->cs_over) g->cs_over->Release();
    if (g->cs_comp) g->cs_comp->Release();
    if (g->cs_final) g->cs_final->Release();
    if (g->ctx) g->ctx->Release();
    if (g->dev) g->dev->Release();
    delete g;
}

EXPORT int pg_add_layer(void* p) {
    Gpu* g = (Gpu*)p;
    if (!g) return -1;
    Layer l;
    static std::vector<float> zeros;
    if (zeros.size() < (size_t)g->W * g->H * 4) zeros.assign((size_t)g->W * g->H * 4, 0.0f);
    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = (UINT)g->W * g->H * 16;
    bd.Usage = D3D11_USAGE_DEFAULT;
    bd.BindFlags = D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE;
    bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = 16;
    D3D11_SUBRESOURCE_DATA init{};
    init.pSysMem = zeros.data();
    g->dev->CreateBuffer(&bd, &init, &l.buf);
    D3D11_UNORDERED_ACCESS_VIEW_DESC ud{};
    ud.Format = DXGI_FORMAT_UNKNOWN;
    ud.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    ud.Buffer.NumElements = (UINT)g->W * g->H;
    g->dev->CreateUnorderedAccessView(l.buf, &ud, &l.uav);
    {
        D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
        sd.Format = DXGI_FORMAT_UNKNOWN;
        sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        sd.Buffer.NumElements = (UINT)g->W * g->H;
        g->dev->CreateShaderResourceView(l.buf, &sd, &l.srv);
    }
    g->layers.push_back(l);
    return (int)g->layers.size() - 1;
}

EXPORT int pg_layer_count(void* p) { Gpu* g = (Gpu*)p; return g ? (int)g->layers.size() : 0; }

EXPORT void pg_clear_layer(void* p, int idx, float r, float g_, float b, float a) {
    Gpu* g = (Gpu*)p;
    if (!g || idx < 0 || idx >= (int)g->layers.size()) return;
    static std::vector<float> px;
    if (px.size() < (size_t)g->W * g->H * 4) px.resize((size_t)g->W * g->H * 4);
    for (size_t i = 0; i < (size_t)g->W * g->H; ++i) {
        px[i * 4 + 0] = r * a; px[i * 4 + 1] = g_ * a; px[i * 4 + 2] = b * a; px[i * 4 + 3] = a;
    }
    g->ctx->UpdateSubresource(g->layers[idx].buf, 0, nullptr, px.data(), 0, 0);
}

EXPORT void pg_set_region(void* p, const int* region, int use) {
    Gpu* g = (Gpu*)p;
    if (!g) return;
    if (!region || !use) { g->hasRegion = false; return; }
    UINT npix = (UINT)g->W * (UINT)g->H;
    if (!g->regionBuf) {
        g->regionBuf = make_buf(g->dev, npix * 4, 4, D3D11_BIND_SHADER_RESOURCE,
                                D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
        D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
        sd.Format = DXGI_FORMAT_UNKNOWN;
        sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        sd.Buffer.NumElements = npix;
        g->dev->CreateShaderResourceView(g->regionBuf, &sd, &g->regionSrv);
    }
    g->ctx->UpdateSubresource(g->regionBuf, 0, nullptr, region, 0, 0);
    g->hasRegion = true;
}

EXPORT int pg_add_tip(void* p, const float* data, int w, int h) {
    Gpu* g = (Gpu*)p;
    if (!g || !data || w <= 0 || h <= 0) return -1;
    Tip t;
    t.base = (int)g->tipdata.size();
    t.w = w; t.h = h;
    g->tipdata.insert(g->tipdata.end(), data, data + (size_t)w * h);
    g->tips.push_back(t);
    if (g->tipSrv) { g->tipSrv->Release(); g->tipSrv = nullptr; }
    if (g->tipbuf) { g->tipbuf->Release(); g->tipbuf = nullptr; }
    UINT n = (UINT)g->tipdata.size();
    g->tipbuf = make_buf(g->dev, n * 4, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
    sd.Format = DXGI_FORMAT_UNKNOWN;
    sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    sd.Buffer.NumElements = n;
    g->dev->CreateShaderResourceView(g->tipbuf, &sd, &g->tipSrv);
    g->ctx->UpdateSubresource(g->tipbuf, 0, nullptr, g->tipdata.data(), 0, 0);
    return (int)g->tips.size() - 1;
}

static void ensure_stamps(Gpu* g, UINT n) {
    if (g->stamps && g->stampsSrv) {
        D3D11_BUFFER_DESC d{}; g->stamps->GetDesc(&d);
        if (d.ByteWidth >= n * 32) return;
        g->stampsSrv->Release(); g->stamps->Release(); g->stampsSrv = nullptr; g->stamps = nullptr;
    }
    UINT cap = std::max(65536u, n);
    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = cap * 32;
    bd.Usage = D3D11_USAGE_DEFAULT;
    bd.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = 16;
    HRESULT hr = g->dev->CreateBuffer(&bd, nullptr, &g->stamps);
    if (FAILED(hr) || !g->stamps) {
        printf("[gpu] stamps buffer failed cap=%u hr=%08lx\n", cap, (unsigned long)hr);
        fflush(stdout);
        g->stamps = nullptr; g->stampsSrv = nullptr;
        return;
    }
    D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
    sd.Format = DXGI_FORMAT_UNKNOWN;
    sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    sd.Buffer.NumElements = cap * 2;
    g->dev->CreateShaderResourceView(g->stamps, &sd, &g->stampsSrv);
}

static int lut_slot_for(Gpu* g, int preset, float hardness) {
    for (int i = 0; i < g->lut_n; ++i)
        if (g->lut_preset[i] == preset && g->lut_hard[i] == hardness) return i;
    if (g->lut_n >= 32) return 0;
    int slot = g->lut_n++;
    float tbl[256];
    build_tip_table(tbl, hardness, preset);
    D3D11_BOX box{};
    box.left = 0; box.right = 1024;
    // 槽位偏移：UpdateSubresource 的 box 以字节计，直接写到该槽起点
    box.left = (UINT)(slot * 1024); box.right = (UINT)(slot * 1024 + 1024);
    box.top = 0; box.bottom = 1; box.front = 0; box.back = 1;
    g->ctx->UpdateSubresource(g->lutb, 0, &box, tbl, 0, 0);
    g->lut_preset[slot] = preset; g->lut_hard[slot] = hardness;
    return slot;
}

static float g_color_dyn = 0.0f;   // 逐 stamp 色彩微差强度（GPU 侧）
static float g_dyn_scatter = 0.0f, g_dyn_size = 0.0f, g_dyn_alpha = 0.0f, g_dyn_angle = 0.0f, g_dyn_taper = 0.0f;

static inline float hash3(uint32_t x, uint32_t y, uint32_t s) {
    uint32_t h = x * 374761393u + y * 668265263u + s * 2246822519u;
    h = (h ^ (h >> 13)) * 1274126177u;
    h ^= h >> 16;
    return (float)(h & 0xffffffu) / 16777216.0f;
}

static inline float smoothstepf(float e0, float e1, float x) {
    if (e0 == e1) return x >= e1 ? 1.0f : 0.0f;
    float t = (x - e0) / (e1 - e0);
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return t * t * (3.0f - 2.0f * t);
}

struct StampOut { float x, y, R, a, dx, dy, bucket, pad; };

static void expand_stroke(const float* pts, int n, float size, std::vector<StampOut>& out, unsigned seed) {
    const float baseR = size * 0.5f;
    float total = 0.0f;
    if (n > 1) {
        for (int i = 1; i < n; ++i) {
            float dx = pts[i * 3 + 0] - pts[(i - 1) * 3 + 0];
            float dy = pts[i * 3 + 1] - pts[(i - 1) * 3 + 1];
            total += sqrtf(dx * dx + dy * dy);
        }
    }
    unsigned si = 0;
    float arc = 0.0f;
    auto push = [&](float x, float y, float press, float dx, float dy, float tpos) {
        float pr = 0.30f + 0.70f * press;
        float pa = 0.15f + 0.85f * press;
        unsigned k = si++;
        if (g_dyn_taper > 0.0f && total > 1e-6f) {
            float f0 = tpos / g_dyn_taper, f1 = (1.0f - tpos) / g_dyn_taper;
            if (f0 > 1.0f) f0 = 1.0f;
            if (f1 > 1.0f) f1 = 1.0f;
            float f = smoothstepf(0.0f, 1.0f, f0) * smoothstepf(0.0f, 1.0f, f1);
            pr *= 0.35f + 0.65f * f;
        }
        if (g_dyn_size > 0.0f) pr *= 1.0f + g_dyn_size * (hash3(seed, k, 13u) * 2.0f - 1.0f);
        if (g_dyn_alpha > 0.0f) pa *= 1.0f + g_dyn_alpha * (hash3(seed, k, 14u) * 2.0f - 1.0f);
        if (g_dyn_scatter > 0.0f) {
            float ang = hash3(seed, k, 11u) * 6.2831853f;
            float rad = hash3(seed, k, 12u) * g_dyn_scatter * baseR * pr;
            x += cosf(ang) * rad;
            y += sinf(ang) * rad;
        }
        if (g_dyn_angle > 0.0f) {
            float aa = g_dyn_angle * (hash3(seed, k, 15u) * 2.0f - 1.0f);
            float ca = cosf(aa), sa = sinf(aa);
            float nx = dx * ca - dy * sa, ny = dx * sa + dy * ca;
            dx = nx; dy = ny;
        }
        if (pr < 0.0f) pr = 0.0f;
        if (pa < 0.0f) pa = 0.0f;
        if (pa > 1.0f) pa = 1.0f;
        float bucket = 0.0f;
        if (g_color_dyn > 0.0f) {
            float mk = 1.0f + g_color_dyn * (hash3(seed, k, 16u) * 2.0f - 1.0f);
            float tk = g_color_dyn * 0.25f * (hash3(seed, k, 17u) * 2.0f - 1.0f);
            float bm = (mk - (1.0f - g_color_dyn)) / (2.0f * g_color_dyn) * 15.0f + 0.5f;
            float bw = (tk + g_color_dyn * 0.25f) / (g_color_dyn * 0.5f) * 15.0f + 0.5f;
            int ib = (int)bm, iw = (int)bw;
            if (ib < 0) ib = 0; else if (ib > 15) ib = 15;
            if (iw < 0) iw = 0; else if (iw > 15) iw = 15;
            bucket = (float)((ib << 4) | iw);
        }
        out.push_back({ x, y, baseR * pr, pa, dx, dy, bucket, 0.0f });
    };
    if (n == 1) { push(pts[0], pts[1], pts[2], 1.0f, 0.0f, 0.0f); return; }
    for (int i = 1; i < n; ++i) {
        float x0 = pts[(i - 1) * 3 + 0], y0 = pts[(i - 1) * 3 + 1], p0 = pts[(i - 1) * 3 + 2];
        float x1 = pts[i * 3 + 0], y1 = pts[i * 3 + 1], p1 = pts[i * 3 + 2];
        float dx = x1 - x0, dy = y1 - y0;
        float len = sqrtf(dx * dx + dy * dy);
        float ux = len > 1e-6f ? dx / len : 1.0f, uy = len > 1e-6f ? dy / len : 0.0f;
        float step = std::max(0.6f, baseR * 0.30f);
        int steps = (int)floorf(len / step);
        if (steps < 1) steps = 1;
        for (int s = 0; s <= steps; ++s) {
            float t = (float)s / (float)steps;
            float ap = total > 1e-6f ? (arc + len * t) / total : 0.0f;
            push(x0 + dx * t, y0 + dy * t, p0 + (p1 - p0) * t, ux, uy, ap);
        }
        arc += len;
    }
}

static void upload_params(Gpu* g, Params& p) {
    static unsigned char tmp[256];
    memset(tmp, 0, sizeof(tmp));
    memcpy(tmp, &p, sizeof(Params));
    g->ctx->UpdateSubresource(g->cb, 0, nullptr, tmp, 0, 0);
}

EXPORT void pg_clear_rect(void* p, int idx, int x0, int y0, int x1, int y1) {
    Gpu* g = (Gpu*)p;
    if (!g || idx < 0 || idx >= (int)g->layers.size()) return;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > g->W - 1) x1 = g->W - 1;
    if (y1 > g->H - 1) y1 = g->H - 1;
    if (x1 < x0 || y1 < y0) return;
    std::vector<float> row((size_t)(x1 - x0 + 1) * 4, 0.0f);
    Layer& L = g->layers[idx];
    for (int y = y0; y <= y1; ++y) {
        D3D11_BOX box{};
        box.left = (UINT)((size_t)y * g->W * 16 + (size_t)x0 * 16);
        box.right = (UINT)((size_t)y * g->W * 16 + (size_t)(x1 + 1) * 16);
        box.top = 0; box.bottom = 1; box.front = 0; box.back = 1;
        g->ctx->UpdateSubresource(L.buf, 0, &box, row.data(), 0, 0);
    }
}

EXPORT void pg_set_wet(void* p, float amount) {
    Gpu* g = (Gpu*)p;
    if (g) g->wet = amount;
}

EXPORT void pg_set_color_dynamics(void* p, float amount) {
    Gpu* g = (Gpu*)p;
    if (!g) return;
    g->color_dyn = amount > 0.0f ? amount : 0.0f;
}

EXPORT void pg_set_dynamics(void* p, float scatter, float size_jit, float alpha_jit, float angle_jit, float taper) {
    Gpu* g = (Gpu*)p;
    if (!g) return;
    g->dyn_scatter = scatter; g->dyn_size = size_jit; g->dyn_alpha = alpha_jit;
    g->dyn_angle = angle_jit; g->dyn_taper = taper;
}

EXPORT const char* pg_prof(void) {
    static char buf[256];
    snprintf(buf, sizeof(buf), "expand %.0f upload %.0f splat %.0f over %.0f ms",
             g_t_expand, g_t_upload, g_t_splat, g_t_over);
    return buf;
}

EXPORT int pg_stroke_batch(void* p, int layer, const float* pts, const int* offs,
                           int n_strokes, const float* params, const int* rids) {
    Gpu* g = (Gpu*)p;
    if (!g || !pts || n_strokes <= 0) return 0;
    g_dyn_scatter = g->dyn_scatter; g_dyn_size = g->dyn_size; g_dyn_alpha = g->dyn_alpha;
    g_dyn_angle = g->dyn_angle; g_dyn_taper = g->dyn_taper; g_color_dyn = g->color_dyn;
    if (layer < 0 || layer >= (int)g->layers.size()) return 0;
    Layer& L = g->layers[layer];
    if (g->wet > 0.0f && g->snap) g->ctx->CopyResource(g->snap, L.buf);
    const int CHUNK = 256;
    std::vector<StampOut> stamps;
    std::vector<float> flat;
    struct Plan { int start, count, bx0, by0, bx1, by1, use_tip, tip_base, tip_w, tip_h, lut_slot, region_id, branch, cosn; unsigned seed; float kw, tw, tc, thr; float opacity, color[3]; };
    std::vector<Plan> plans;
    for (int c0 = 0; c0 < n_strokes; c0 += CHUNK) {
        int c1 = std::min(n_strokes, c0 + CHUNK);
        plans.clear();
        flat.clear();
        double t0 = now_ms();
        for (int i = c0; i < c1; ++i) {
            int b = offs[i], e = offs[i + 1];
            stamps.clear();
            expand_stroke(pts + b, (e - b) / 3, params[i * 8 + 0], stamps, (unsigned)i);
            if (stamps.empty()) continue;
            Plan sp{};
            sp.seed = (unsigned)i;
            sp.start = (int)(flat.size() / 8);
            sp.count = (int)stamps.size();
            sp.region_id = rids ? rids[i] : 0;
            sp.opacity = params[i * 8 + 1];
            sp.color[0] = params[i * 8 + 2];
            sp.color[1] = params[i * 8 + 3];
            sp.color[2] = params[i * 8 + 4];
            int preset = (int)(params[i * 8 + 7] + 0.5f);
            sp.use_tip = preset >= 100 ? 1 : 0;
            sp.lut_slot = 0;
            if (sp.use_tip) {
                int ti = preset - 100;
                if (ti < 0 || ti >= (int)g->tips.size()) { sp.use_tip = 0; }
                else { sp.tip_base = g->tips[ti].base; sp.tip_w = g->tips[ti].w; sp.tip_h = g->tips[ti].h; }
            }
            sp.branch = 0; sp.cosn = 9; sp.kw = 0.5f; sp.tw = 0.55f; sp.tc = 0.45f; sp.thr = 0.62f;
            if (!sp.use_tip) {
                sp.lut_slot = lut_slot_for(g, preset, params[i * 8 + 5]);
                if (preset == 2) { sp.branch = 1; sp.cosn = 9; sp.kw = 0.42f; sp.tw = 0.70f; sp.tc = 0.30f; }
                else if (preset == 8) { sp.branch = 1; sp.cosn = 17; sp.kw = 0.50f; sp.tw = 0.55f; sp.tc = 0.45f; }
                else if (preset == 3) { sp.branch = 2; sp.thr = 0.42f; }
                else if (preset == 6) { sp.branch = 2; sp.thr = 0.28f; }
            }
            float ext = (sp.use_tip || sp.branch != 0) ? 1.3f : 1.0f;
            float minx = 1e9f, miny = 1e9f, maxx = -1e9f, maxy = -1e9f;
            for (auto& st : stamps) {
                minx = std::min(minx, st.x - st.R * ext); maxx = std::max(maxx, st.x + st.R * ext);
                miny = std::min(miny, st.y - st.R * ext); maxy = std::max(maxy, st.y + st.R * ext);
            }
            sp.bx0 = std::max(0, (int)floorf(minx)); sp.bx1 = std::min(g->W - 1, (int)ceilf(maxx));
            sp.by0 = std::max(0, (int)floorf(miny)); sp.by1 = std::min(g->H - 1, (int)ceilf(maxy));
            if (sp.bx0 > sp.bx1 || sp.by0 > sp.by1) continue;
            for (auto& st : stamps) {
                flat.push_back(st.x); flat.push_back(st.y); flat.push_back(st.R); flat.push_back(st.a);
                flat.push_back(st.dx); flat.push_back(st.dy); flat.push_back(st.bucket); flat.push_back(0.0f);
            }
            plans.push_back(sp);
        }
        double t1 = now_ms();
        g_t_expand += t1 - t0;
        if (plans.empty()) continue;
        UINT total = (UINT)(flat.size() / 8);
        ensure_stamps(g, total);
        if (!g->stamps || !g->stampsSrv) continue;
        D3D11_BOX sbox{};
        sbox.left = 0; sbox.right = (UINT)(flat.size() * 4);
        sbox.top = 0; sbox.bottom = 1; sbox.front = 0; sbox.back = 1;
        g->ctx->UpdateSubresource(g->stamps, 0, &sbox, flat.data(), 0, 0);
        double t2 = now_ms();
        g_t_upload += t2 - t1;

        for (auto& sp : plans) {
            Params cp{};
            memset(&cp, 0, sizeof(cp));
            cp.color[0] = sp.color[0]; cp.color[1] = sp.color[1]; cp.color[2] = sp.color[2]; cp.color[3] = 1.0f;
            cp.W = g->W; cp.H = g->H;
            cp.use_region = g->hasRegion ? 1 : 0;
            cp.region_id = sp.region_id;
            cp.nstamps = (UINT)sp.count;
            cp.stamp_base = (UINT)sp.start;
            cp.opacity = sp.opacity;
            cp.wet = g->wet;
            cp.bx0 = sp.bx0; cp.by0 = sp.by0; cp.bx1 = sp.bx1; cp.by1 = sp.by1;
            cp.use_tip = sp.use_tip;
            cp.tip_base = sp.tip_base; cp.tip_w = sp.tip_w; cp.tip_h = sp.tip_h;
            cp.lut_slot = sp.lut_slot;
            cp.branch = sp.branch; cp.cosn = sp.cosn;
            cp.seed = 0u;   // CPU band 路径 jitter_color(..., 0u, k)（paint_core.cpp:760）
            cp.color_dyn = g_color_dyn;
            cp.use_color = g_color_dyn > 0.0f ? 1u : 0u;
            cp.kw = sp.kw; cp.tw = sp.tw; cp.tc = sp.tc; cp.thr = sp.thr;
            UINT bs = (cp.nstamps + 255u) / 256u; if (bs < 1) bs = 1; if (bs > 1024) bs = 1024;
            UINT bo = (UINT)((sp.bx1 - sp.bx0 + 1) * (sp.by1 - sp.by0 + 1) + 255) / 256u;
            if (bo < 1) bo = 1; if (bo > 1024) bo = 1024;
            cp.bs = bs; cp.bo = bo;
            cp.th_splat = bs * 256u;
            cp.th_over = bo * 256u;
            upload_params(g, cp);
            ID3D11ShaderResourceView* srvs[6] = { g->stampsSrv,
                                                  g->hasRegion ? g->regionSrv : g->dummySrv,
                                                  sp.use_tip ? g->tipSrv : g->dummySrv,
                                                  g->lutSrv,
                                                  g->dummySrv,
                                                  (g->wet > 0.0f && g->snapSrv) ? g->snapSrv : g->dummySrv };
            ID3D11UnorderedAccessView* uavs[2] = { g->scratchUav, L.uav };
            g->ctx->CSSetShaderResources(0, 6, srvs);
            g->ctx->CSSetUnorderedAccessViews(0, 2, uavs, nullptr);
            g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
            g->ctx->CSSetShader(g->cs_splat, nullptr, 0);
            g->ctx->Dispatch(cp.bs, 1, 1);
            g->ctx->CSSetShader(g->cs_over, nullptr, 0);
            g->ctx->Dispatch(cp.bo, 1, 1);
        }
        g_t_splat += now_ms() - t2;
    }
    return 1;
}

EXPORT int pg_comp_begin(void* p, int blend) {
    Gpu* g = (Gpu*)p;
    if (!g) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    static std::vector<float> zeros;
    if (zeros.size() < (size_t)npix * 4) zeros.assign((size_t)npix * 4, 0.0f);
    g->ctx->UpdateSubresource(g->acc, 0, nullptr, zeros.data(), 0, 0);
    return 1;
}

EXPORT int pg_composite_layer(void* p, int idx, int blend) {
    Gpu* g = (Gpu*)p;
    if (!g || idx < 0 || idx >= (int)g->layers.size()) return 0;
    Params cp{};
    memset(&cp, 0, sizeof(cp));
    cp.W = g->W; cp.H = g->H;
    cp.blend_mode = blend;
    cp.th_over = 256u * 1024u;
    upload_params(g, cp);
    UINT blocks = ((UINT)g->W * g->H + 255u) / 256u;
    if (blocks > 1024) blocks = 1024;
    cp.th_over = blocks * 256u;
    upload_params(g, cp);
    ID3D11ShaderResourceView* srvs[5] = { g->stampsSrv, g->dummySrv, g->dummySrv, g->lutSrv, g->layers[idx].srv };
    ID3D11UnorderedAccessView* nul[2] = { nullptr, nullptr };
    // 顺序很关键：图层刚作为 UAV(u1) 写过，必须先解绑 UAV，再绑 SRV(t4)；
    // 反过来 D3D11 会在绑 UAV 的瞬间静默丢弃 SRV，导致下一次合成采样到空 → 全黑。
    g->ctx->CSSetUnorderedAccessViews(0, 2, nul, nullptr);
    g->ctx->CSSetShaderResources(0, 5, srvs);
    g->ctx->CSSetUnorderedAccessViews(2, 1, &g->accUav, nullptr);
    g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
    g->ctx->CSSetShader(g->cs_comp, nullptr, 0);
    g->ctx->Dispatch(blocks, 1, 1);
    return 1;
}

EXPORT int pg_comp_checkpoint(void* p) {
    Gpu* g = (Gpu*)p;
    if (!g) return 0;
    g->ctx->CopyResource(g->base, g->acc);
    return 1;
}

EXPORT int pg_comp_restore(void* p) {
    Gpu* g = (Gpu*)p;
    if (!g) return 0;
    g->ctx->CopyResource(g->acc, g->base);
    return 1;
}

EXPORT int pg_read_rgba8(void* p, unsigned char* out) {
    Gpu* g = (Gpu*)p;
    if (!g || !out) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    Params cp{};
    memset(&cp, 0, sizeof(cp));
    cp.W = g->W; cp.H = g->H;
    UINT blocks = (npix + 255u) / 256u;
    if (blocks > 1024) blocks = 1024;
    cp.th_over = blocks * 256u;
    upload_params(g, cp);
    ID3D11UnorderedAccessView* nul[2] = { nullptr, nullptr };
    g->ctx->CSSetUnorderedAccessViews(0, 2, nul, nullptr);
    g->ctx->CSSetUnorderedAccessViews(2, 1, &g->accUav, nullptr);
    g->ctx->CSSetUnorderedAccessViews(3, 1, &g->acc8Uav, nullptr);
    g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
    g->ctx->CSSetShader(g->cs_final, nullptr, 0);
    g->ctx->Dispatch(blocks, 1, 1);
    ID3D11Buffer* rb = make_buf(g->dev, npix * 4, 4, 0, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, D3D11_CPU_ACCESS_READ);
    g->ctx->CopyResource(rb, g->acc8);
    D3D11_MAPPED_SUBRESOURCE ms{};
    if (SUCCEEDED(g->ctx->Map(rb, 0, D3D11_MAP_READ, 0, &ms))) {
        memcpy(out, ms.pData, (size_t)npix * 4);
        g->ctx->Unmap(rb, 0);
    }
    rb->Release();
    return 1;
}

EXPORT int pg_read_acc(void* p, float* out) {
    Gpu* g = (Gpu*)p;
    if (!g || !out) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    ID3D11Buffer* rb = make_buf(g->dev, npix * 16, 16, 0, 0, D3D11_CPU_ACCESS_READ);
    g->ctx->CopyResource(rb, g->acc);
    D3D11_MAPPED_SUBRESOURCE ms{};
    if (SUCCEEDED(g->ctx->Map(rb, 0, D3D11_MAP_READ, 0, &ms))) {
        memcpy(out, ms.pData, (size_t)npix * 16);
        g->ctx->Unmap(rb, 0);
    }
    rb->Release();
    return 1;
}

EXPORT int pg_read_layer(void* p, int idx, float* out) {
    Gpu* g = (Gpu*)p;
    if (!g || idx < 0 || idx >= (int)g->layers.size()) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    ID3D11Buffer* rb = make_buf(g->dev, npix * 16, 16, 0, 0, D3D11_CPU_ACCESS_READ);
    g->ctx->CopyResource(rb, g->layers[idx].buf);
    D3D11_MAPPED_SUBRESOURCE ms{};
    if (SUCCEEDED(g->ctx->Map(rb, 0, D3D11_MAP_READ, 0, &ms))) {
        memcpy(out, ms.pData, (size_t)npix * 16);
        g->ctx->Unmap(rb, 0);
    }
    rb->Release();
    return 1;
}
