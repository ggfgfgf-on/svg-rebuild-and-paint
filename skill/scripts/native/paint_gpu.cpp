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
    int cos_off; int pad4;
    uint seed; float cpad1;
    uint fill_n, fill_ns; int use_fill_grad; float fx1, fy1, fx2, fy2;
};
cbuffer P : register(b0) { Params p; };
StructuredBuffer<float> lutbuf : register(t3);
StructuredBuffer<float> cosT : register(t6);   // 预计算的 cos(t*cosn) 表（cosn=9 / 17 各 2049 项）
StructuredBuffer<float4> srcLayer : register(t4);
StructuredBuffer<float4> snap : register(t5);
RWStructuredBuffer<float4> accbuf : register(u2);

// 每个 stamp = 24 个 float（6 个 float4）：
//  a=(x,y,R,alpha) b=(dx,dy,kLocal,pad) c=(r,g,b,opacity)
//  d=(branch,lut_slot,cosn,kw) e=(tw,tc,thr,region_id) f=(use_tip,tip_base,tip_w,tip_h)
StructuredBuffer<float> spr : register(t0);
void get_sprite(uint i, out float4 a, out float4 b, out float4 c, out float4 d, out float4 e, out float4 f) {
    uint o = i * 24u;
    a = float4(spr[o + 0u], spr[o + 1u], spr[o + 2u], spr[o + 3u]);
    b = float4(spr[o + 4u], spr[o + 5u], spr[o + 6u], spr[o + 7u]);
    c = float4(spr[o + 8u], spr[o + 9u], spr[o + 10u], spr[o + 11u]);
    d = float4(spr[o + 12u], spr[o + 13u], spr[o + 14u], spr[o + 15u]);
    e = float4(spr[o + 16u], spr[o + 17u], spr[o + 18u], spr[o + 19u]);
    f = float4(spr[o + 20u], spr[o + 21u], spr[o + 22u], spr[o + 23u]);
}
StructuredBuffer<int> region : register(t1);
StructuredBuffer<float> tips : register(t2);
RWStructuredBuffer<uint> scratch : register(u0);
RWStructuredBuffer<float4> layer : register(u1);
StructuredBuffer<float> maskbuf : register(t7);
StructuredBuffer<float2> fillpts : register(t8);
StructuredBuffer<float> fillstops : register(t9);
StructuredBuffer<float> fillmeta : register(t10);
StructuredBuffer<float2> fillholes : register(t11);
StructuredBuffer<uint> fillholeoff : register(t12);
StructuredBuffer<float> gradstops : register(t11);
StructuredBuffer<float> gradmeta : register(t12);

inline void splat_key(uint idx, float a, uint k) {
    uint a16 = (uint)(saturate(a) * 65535.0f + 0.5f);
    InterlockedMax(scratch[idx], (a16 << 16) | (65535u - min(k, 65535u)));
}

[numthreads(256,1,1)]
void cs_splat(uint3 dt : SV_DispatchThreadID) {
    uint i_end = p.stamp_base + p.nstamps;
    for (uint i = p.stamp_base + dt.x; i < i_end; i += p.th_splat) {
        float4 a, b, c, d, e, f;
        get_sprite(i, a, b, c, d, e, f);
        float posx = a.x, posy = a.y, R = a.z, alpha = a.w;
        float dirx = b.x, diry = b.y;
        int use_tip = (int)f.x, branch = (int)d.x;
        int tip_base = (int)f.y, tip_w = (int)f.z, tip_h = (int)f.w;
        int lut_slot = (int)d.y, cosn = (int)d.z, region_id = (int)e.w;
        float kw = d.w, tw = e.x, tc = e.y, thr = e.z;
        float ext = use_tip ? 1.3f : 1.0f;
        int x0 = max(0, (int)floor(posx - R * ext)), x1 = min((int)p.W - 1, (int)ceil(posx + R * ext));
        int y0 = max(0, (int)floor(posy - R * ext)), y1 = min((int)p.H - 1, (int)ceil(posy + R * ext));
        float inv = 1.0f / max(1e-6f, R * R);
        if (use_tip) {
            float tipInv = 1.0f / max(1e-6f, R * 2.0f);
            int tw1 = tip_w - 1, th1 = tip_h - 1;
            for (int y = y0; y <= y1; ++y) {
                float dy = (float)y + 0.5f - posy;
                for (int x = x0; x <= x1; ++x) {
                    float dx = (float)x + 0.5f - posx;
                    if (dx * dx + dy * dy > R * R * 1.69f) continue;
                    float u = (dx * dirx + dy * diry) * tipInv + 0.5f;
                    float v = (-dx * diry + dy * dirx) * tipInv + 0.5f;
                    if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) continue;
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != region_id && rv != -2) continue; }
                    int ix = (int)(u * tw1 + 0.5f);
                    int iy = (int)(v * th1 + 0.5f);
                    float av = tips[tip_base + iy * tip_w + ix] * alpha;
                    if (av > 0.0f) splat_key(idx, av, i - p.stamp_base);
                }
            }
        } else if (branch == 1) {
            const float invZ = 1.0f / max(1e-6f, R);
            const float inv125 = 1.0f / max(1e-6f, R * 1.25f);
            const float invkw = 1.0f / max(1e-6f, R * kw);
            const float rowOff = 0.5f - posy;
            for (int y = y0; y <= y1; ++y) {
                float dy = (float)y + rowOff;
                float along = ((float)x0 + 0.5f - posx) * dirx + dy * diry;
                float perp = -((float)x0 + 0.5f - posx) * diry + dy * dirx;
                for (int x = x0; x <= x1; ++x, along += dirx, perp -= diry) {
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != region_id && rv != -2) continue; }
                    float aa = along * inv125, bb = perp * invkw;
                    float e2 = aa * aa + bb * bb;
                    if (e2 >= 1.0f) continue;
                    float base;
                    if (e2 <= 0.49f) base = 1.0f;
                    else { float ee = sqrt(e2); base = 1.0f - smoothstep(0.70f, 1.0f, ee); }
                    float gi_f = (perp * invZ + 0.62f) * (2048.0f / 1.24f);
                    int gi = (int)gi_f;
                    if (gi < 0) gi = 0;
                    if (gi > 2047) gi = 2047;
                    float fr = gi_f - (float)gi;
                    if (fr < 0.0f) fr = 0.0f;
                    if (fr > 1.0f) fr = 1.0f;
                    float t0 = -0.62f + 1.24f * (float)gi / 2048.0f;
                    float t1 = -0.62f + 1.24f * (float)(gi + 1) / 2048.0f;
                    float g0 = cos(t0 * (float)cosn), g1 = cos(t1 * (float)cosn);
                    float gro = g0 + (g1 - g0) * fr;
                    float av = base * (tw + tc * gro) * alpha;
                    if (av > 0.0f) splat_key(idx, av, i - p.stamp_base);
                }
            }
        } else if (branch == 2) {
            for (int y = y0; y <= y1; ++y) {
                for (int x = x0; x <= x1; ++x) {
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != region_id && rv != -2) continue; }
                    float dx = (float)x + 0.5f - posx, dy = (float)y + 0.5f - posy;
                    float dist = sqrt(dx * dx + dy * dy);
                    if (dist > R * 1.3f) continue;
                    uint xh = (uint)x, yh = (uint)y, sh = 0u;
                    uint hh = xh * 374761393u + yh * 668265263u + sh * 2246822519u;
                    hh = (hh ^ (hh >> 13u)) * 1274126177u;
                    hh ^= hh >> 16u;
                    float rnd = (float)(hh & 0xFFFFFFu) / 16777216.0f;
                    int kb = (int)min(255.0f, dist / R * 255.0f);
                    float av = rnd > thr ? lutbuf[lut_slot * 256 + kb] * alpha : 0.0f;
                    if (av > 0.0f) splat_key(idx, av, i - p.stamp_base);
                }
            }
        } else {
            for (int y = y0; y <= y1; ++y) {
                for (int x = x0; x <= x1; ++x) {
                    uint idx = (uint)y * p.W + (uint)x;
                    if (p.use_region) { int rv = region[idx]; if (rv != region_id && rv != -2) continue; }
                    float dx = (float)x + 0.5f - posx, dy = (float)y + 0.5f - posy;
                    float e2 = (dx * dx + dy * dy) * inv;
                    int kb = (int)(e2 * 255.0f);
                    if (kb > 255) kb = 255;
                    float av = lutbuf[lut_slot * 256 + kb] * alpha;
                    if (av > 0.0f) splat_key(idx, av, i - p.stamp_base);
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
)HLSL"
R"HLSL(
void cs_over(uint3 dt : SV_DispatchThreadID) {
    int bw = p.bx1 - p.bx0 + 1, bh = p.by1 - p.by0 + 1;
    uint n = (uint)(bw * bh);
    for (uint t = dt.x; t < n; t += p.th_over) {
        int x = p.bx0 + (int)(t % (uint)bw);
        int y = p.by0 + (int)(t / (uint)bw);
        uint idx = (uint)y * p.W + (uint)x;
        uint bits = scratch[idx];
        if (bits == 0u) continue;
        uint win = p.stamp_base + (65535u - (bits & 0xFFFFu));
        float4 a, b, c, d, e, f;
        get_sprite(win, a, b, c, d, e, f);
        float raw_a = (float)(bits >> 16) / 65535.0f;
        float sa = raw_a * c.w;
        if (sa > 0.0f) {
            bool ok = true;
            if (p.use_region) { int rv = region[idx]; ok = (rv == (int)e.w || rv == -2); }
            if (ok) {
                float3 col = c.rgb;
                if (gradmeta[0] > 0.5f) {
                    uint ns = (uint)gradmeta[1];
                    float2 q = float2((float)x + 0.5f, (float)y + 0.5f);
                    int gmode = (int)gradmeta[7];
                    float t;
                    if (gmode == 1 || gmode == 2) {
                        float cx = gradmeta[8], cy = gradmeta[9], rr = max(1e-6f, gradmeta[10]);
                        float fx = gradmeta[11], fy = gradmeta[12];
                        float2 qq = q;
                        if (gmode == 2) {
                            qq = float2(gradmeta[13] * q.x + gradmeta[15] * q.y + gradmeta[17],
                                        gradmeta[14] * q.x + gradmeta[16] * q.y + gradmeta[18]);
                        }
                        float2 d = qq - float2(fx, fy);
                        float2 D = float2(cx - fx, cy - fy);
                        float A = dot(D, D) - rr * rr;
                        float B = -2.0f * dot(d, D);
                        float C = dot(d, d);
                        if (abs(A) < 1e-9f) t = abs(B) < 1e-12f ? 0.0f : -C / B;
                        else { float disc = max(0.0f, B * B - 4.0f * A * C); t = (-B - sqrt(disc)) / (2.0f * A); }
                    } else {
                        float2 gv = float2(gradmeta[4] - gradmeta[2], gradmeta[5] - gradmeta[3]);
                        float gl = dot(gv, gv);
                        t = gl > 1e-9f ? dot(q - float2(gradmeta[2], gradmeta[3]), gv) / gl : 0.0f;
                    }
                    int spread = (int)gradmeta[6];
                    if (spread == 1) { t = abs(t); t = t - floor(t * 0.5f) * 2.0f; if (t > 1.0f) t = 2.0f - t; }
                    else if (spread == 2) { t = t - floor(t); }
                    else t = saturate(t);
                    uint k0 = 0;
                    while (k0 + 1 < ns && gradstops[(k0 + 1) * 5] < t) ++k0;
                    uint k1 = min(ns - 1, k0 + 1);
                    float o0 = gradstops[k0 * 5], o1 = gradstops[k1 * 5];
                    float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
                    col = float3(lerp(gradstops[k0*5+1], gradstops[k1*5+1], u),
                                 lerp(gradstops[k0*5+2], gradstops[k1*5+2], u),
                                 lerp(gradstops[k0*5+3], gradstops[k1*5+3], u));
                }
                if (p.use_color) {
                    uint k = (uint)b.z;
                    float h16 = hash_c(p.seed, k, 16u);
                    float h17 = hash_c(p.seed, k, 17u);
                    float m = 1.0f + p.color_dyn * (h16 * 2.0f - 1.0f);
                    float twk = p.color_dyn * 0.25f * (h17 * 2.0f - 1.0f);
                    col = saturate(float3(col.r * m * (1.0f + twk), col.g * m, col.b * m * (1.0f - twk)));
                }
                if (p.wet > 0.0f) {
                    float4 sn = snap[idx];
                    if (sn.a > 1e-6f) col = lerp(c.rgb, sn.rgb / sn.a, p.wet);
                }
                float4 dd = layer[idx];
                float inv2 = 1.0f - sa;
                layer[idx] = float4(col * sa + dd.rgb * inv2, sa + dd.a * inv2);
            }
        }
        scratch[idx] = 0u;
    }
}

[numthreads(256,1,1)]
void cs_mask(uint3 dt : SV_DispatchThreadID) {
    uint idx = dt.x;
    uint npix = p.W * p.H;
    if (idx >= npix) return;
    layer[idx] *= maskbuf[idx];
}

[numthreads(256,1,1)]
)HLSL"
R"HLSL(
void cs_fill(uint3 dt : SV_DispatchThreadID) {
    uint idx = dt.x;
    uint npix = p.W * p.H;
    if (idx >= npix) return;
    int x = (int)(idx % p.W);
    int y = (int)(idx / p.W);
    float2 q = float2((float)x + 0.5f, (float)y + 0.5f);
    uint fill_n = (uint)fillmeta[0];
    uint fill_ns = (uint)fillmeta[1];
    int use_fill_grad = (int)fillmeta[2];
    float fx1 = fillmeta[3], fy1 = fillmeta[4], fx2 = fillmeta[5], fy2 = fillmeta[6];
    bool inside = false;
    for (uint i = 0, j = fill_n - 1; i < fill_n; j = i++) {
        float2 a = fillpts[i], b = fillpts[j];
        if (((a.y > q.y) != (b.y > q.y)) && (q.x < (b.x - a.x) * (q.y - a.y) / max(1e-9f, b.y - a.y) + a.x))
            inside = !inside;
    }
    uint nh = (uint)fillmeta[21];
    if (inside && nh > 0) {
        for (uint h = 0; h < nh; ++h) {
            uint a0 = fillholeoff[h], a1 = fillholeoff[h + 1];
            bool hin = false;
            if (a1 > a0 + 2) {
                uint hn = a1 - a0;
                for (uint i = 0, j = hn - 1; i < hn; j = i++) {
                    float2 a = fillholes[a0 + i], b = fillholes[a0 + j];
                    if (((a.y > q.y) != (b.y > q.y)) && (q.x < (b.x - a.x) * (q.y - a.y) / max(1e-9f, b.y - a.y) + a.x))
                        hin = !hin;
                }
                if (hin) inside = !inside;
            }
        }
    }
    if (!inside) return;
    float3 col = p.color.rgb;
    float ca = 1.0f;
    if (use_fill_grad && fill_ns >= 2) {
        float t;
        int fmode = (int)fillmeta[9];
        if (fmode == 1 || fmode == 2) {
            float cx = fillmeta[10], cy = fillmeta[11], rr = max(1e-6f, fillmeta[12]);
            float fx = fillmeta[13], fy = fillmeta[14];
            float2 qq = q;
            if (fmode == 2) {
                qq = float2(fillmeta[15] * q.x + fillmeta[17] * q.y + fillmeta[19],
                            fillmeta[16] * q.x + fillmeta[18] * q.y + fillmeta[20]);
            }
            float2 d = qq - float2(fx, fy);
            float2 D = float2(cx - fx, cy - fy);
            float A = dot(D, D) - rr * rr;
            float B = -2.0f * dot(d, D);
            float C = dot(d, d);
            if (abs(A) < 1e-9f) t = abs(B) < 1e-12f ? 0.0f : -C / B;
            else { float disc = max(0.0f, B * B - 4.0f * A * C); t = (-B - sqrt(disc)) / (2.0f * A); }
        } else {
            float2 g = float2(fx2 - fx1, fy2 - fy1);
            float gl = dot(g, g);
            t = gl > 1e-9f ? dot(q - float2(fx1, fy1), g) / gl : 0.0f;
        }
        int spread = (int)fillmeta[8];
        if (spread == 1) {
            t = abs(t); t = t - floor(t * 0.5f) * 2.0f; if (t > 1.0f) t = 2.0f - t;
        } else if (spread == 2) {
            t = t - floor(t);
        } else {
            t = saturate(t);
        }
        uint k0 = 0;
        while (k0 + 1 < fill_ns && fillstops[(k0 + 1) * 5] < t) ++k0;
        uint k1 = min(fill_ns - 1, k0 + 1);
        float o0 = fillstops[k0 * 5], o1 = fillstops[k1 * 5];
        float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
        col = float3(lerp(fillstops[k0*5+1], fillstops[k1*5+1], u),
                     lerp(fillstops[k0*5+2], fillstops[k1*5+2], u),
                     lerp(fillstops[k0*5+3], fillstops[k1*5+3], u));
        ca = lerp(fillstops[k0*5+4], fillstops[k1*5+4], u);
    }
    float sa = fillmeta[7] * ca;
    float4 d = layer[idx];
    float inv = 1.0f - sa;
    layer[idx] = float4(col * sa + d.rgb * inv, sa + d.a * inv);
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
    int cos_off, pad4;
    uint32_t seed; float cpad1;
    unsigned int fill_n, fill_ns; int use_fill_grad; float fx1, fy1, fx2, fy2;
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
    ID3D11ComputeShader* cs_mask = nullptr;
    ID3D11ComputeShader* cs_fill = nullptr;
    ID3D11Buffer* fillPts = nullptr;
    ID3D11ShaderResourceView* fillPtsSrv = nullptr;
    UINT fillPtsCap = 0;
    ID3D11Buffer* fillStops = nullptr;
    ID3D11ShaderResourceView* fillStopsSrv = nullptr;
    UINT fillStopsCap = 0;
    ID3D11Buffer* fillMeta = nullptr;
    ID3D11ShaderResourceView* fillMetaSrv = nullptr;
    ID3D11Buffer* fillHolePts = nullptr;
    ID3D11ShaderResourceView* fillHolePtsSrv = nullptr;
    UINT fillHolePtsCap = 0;
    ID3D11Buffer* fillHoleOff = nullptr;
    ID3D11ShaderResourceView* fillHoleOffSrv = nullptr;
    UINT fillHoleOffCap = 0;
    ID3D11Buffer* gradStops = nullptr;
    ID3D11ShaderResourceView* gradStopsSrv = nullptr;
    ID3D11Buffer* gradMeta = nullptr;
    ID3D11ShaderResourceView* gradMetaSrv = nullptr;
    ID3D11Buffer* maskBuf = nullptr;
    ID3D11ShaderResourceView* maskSrv = nullptr;
    ID3D11Buffer* snap = nullptr;
    ID3D11ShaderResourceView* snapSrv = nullptr;
    float wet = 0.0f;
    // 每实例动态参数（此前是文件级 static，多个 painter 会互相覆盖）
    float color_dyn = 0.0f;
    float dyn_scatter = 0.0f, dyn_size = 0.0f, dyn_alpha = 0.0f, dyn_angle = 0.0f, dyn_taper = 0.0f;
    ID3D11Buffer* scratch = nullptr;
    ID3D11UnorderedAccessView* scratchUav = nullptr;
    ID3D11Buffer* stamps = nullptr;
    UINT stamps_cap = 0;
    ID3D11ShaderResourceView* stampsSrv = nullptr;
    ID3D11Buffer* regionBuf = nullptr;
    ID3D11ShaderResourceView* regionSrv = nullptr;
    ID3D11Buffer* dummy = nullptr;
    ID3D11ShaderResourceView* dummySrv = nullptr;
    ID3D11Buffer* cb = nullptr;
    ID3D11Buffer* cosb = nullptr;
    ID3D11ShaderResourceView* cosSrv = nullptr;
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

static unsigned long g_n_disp = 0;
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

static bool ensure_struct(ID3D11Device* dev, ID3D11Buffer*& buf, ID3D11ShaderResourceView*& srv,
                             UINT& cap, UINT need, UINT stride) {
    if (buf && srv && cap >= need && need > 0) return true;
    if (srv) { srv->Release(); srv = nullptr; }
    if (buf) { buf->Release(); buf = nullptr; }
    cap = 0;
    if (need < 1) need = 1;
    buf = make_buf(dev, need * stride, stride, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    if (!buf) return false;
    D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
    sd.Format = DXGI_FORMAT_UNKNOWN;
    sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    sd.Buffer.NumElements = need;
    if (FAILED(dev->CreateShaderResourceView(buf, &sd, &srv))) { buf->Release(); buf = nullptr; return false; }
    cap = need;
    return true;
}


EXPORT void* pg_create(int w, int h) {
    Gpu* g = new Gpu();
    g->W = w; g->H = h;
    D3D_FEATURE_LEVEL fl;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                   D3D11_SDK_VERSION, &g->dev, &fl, &g->ctx);
    if (FAILED(hr)) {
        hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr, 0,
                               D3D11_SDK_VERSION, &g->dev, &fl, &g->ctx);
    }
    if (FAILED(hr)) {
        delete g;
        return nullptr;
    }
    g->cs_splat = compile_cs(g->dev, kHLSL, "cs_splat");
    g->cs_over = compile_cs(g->dev, kHLSL, "cs_over");
    g->cs_comp = compile_cs(g->dev, kHLSL, "cs_composite");
    g->cs_final = compile_cs(g->dev, kHLSL, "cs_final");
    g->cs_mask = compile_cs(g->dev, kHLSL, "cs_mask");
    g->cs_fill = compile_cs(g->dev, kHLSL, "cs_fill");
    if (!g->cs_splat || !g->cs_over || !g->cs_comp || !g->cs_final || !g->cs_mask || !g->cs_fill) { delete g; return nullptr; }
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
    g->maskBuf = make_buf(g->dev, npix * 4, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    if (g->maskBuf) {
        D3D11_SHADER_RESOURCE_VIEW_DESC msd{};
        msd.Format = DXGI_FORMAT_UNKNOWN;
        msd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        msd.Buffer.NumElements = npix;
        g->dev->CreateShaderResourceView(g->maskBuf, &msd, &g->maskSrv);
    }
    g->fillPts = make_buf(g->dev, 8, 8, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    g->fillPtsCap = 1;
    g->fillStops = make_buf(g->dev, 20, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    g->fillStopsCap = 1;
    g->fillMeta = make_buf(g->dev, 24 * 4, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    g->gradStops = make_buf(g->dev, 64 * 5 * 4, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    g->gradMeta = make_buf(g->dev, 24 * 4, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    if (g->fillPts) {
        D3D11_SHADER_RESOURCE_VIEW_DESC fs{};
        fs.Format = DXGI_FORMAT_UNKNOWN; fs.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; fs.Buffer.NumElements = 1;
        g->dev->CreateShaderResourceView(g->fillPts, &fs, &g->fillPtsSrv);
    }
    if (g->fillStops) {
        D3D11_SHADER_RESOURCE_VIEW_DESC ss{};
        ss.Format = DXGI_FORMAT_UNKNOWN; ss.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; ss.Buffer.NumElements = 1;
        g->dev->CreateShaderResourceView(g->fillStops, &ss, &g->fillStopsSrv);
    }
    if (g->fillMeta) {
        D3D11_SHADER_RESOURCE_VIEW_DESC ms{};
        ms.Format = DXGI_FORMAT_UNKNOWN; ms.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; ms.Buffer.NumElements = 24;
        g->dev->CreateShaderResourceView(g->fillMeta, &ms, &g->fillMetaSrv);
    }
    g->fillHolePts = make_buf(g->dev, 8, 8, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    g->fillHolePtsCap = 1;
    g->fillHoleOff = make_buf(g->dev, 4, 4, D3D11_BIND_SHADER_RESOURCE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
    g->fillHoleOffCap = 1;
    if (g->fillHolePts) {
        D3D11_SHADER_RESOURCE_VIEW_DESC hs{};
        hs.Format = DXGI_FORMAT_UNKNOWN; hs.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; hs.Buffer.NumElements = 1;
        g->dev->CreateShaderResourceView(g->fillHolePts, &hs, &g->fillHolePtsSrv);
    }
    if (g->fillHoleOff) {
        D3D11_SHADER_RESOURCE_VIEW_DESC os{};
        os.Format = DXGI_FORMAT_UNKNOWN; os.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; os.Buffer.NumElements = 1;
        g->dev->CreateShaderResourceView(g->fillHoleOff, &os, &g->fillHoleOffSrv);
    }
    if (g->gradStops) {
        D3D11_SHADER_RESOURCE_VIEW_DESC gs{};
        gs.Format = DXGI_FORMAT_UNKNOWN; gs.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; gs.Buffer.NumElements = 320;
        g->dev->CreateShaderResourceView(g->gradStops, &gs, &g->gradStopsSrv);
    }
    if (g->gradMeta) {
        D3D11_SHADER_RESOURCE_VIEW_DESC gm{};
        gm.Format = DXGI_FORMAT_UNKNOWN; gm.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; gm.Buffer.NumElements = 24;
        g->dev->CreateShaderResourceView(g->gradMeta, &gm, &g->gradMetaSrv);
    }
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
    {
        // cos(t*cosn) 预计算表：cosn=9（平笔）与 17（鬃毛）各 2049 项，替换内层两次 cos
        std::vector<float> ct(2 * 2049);
        for (int ci = 0; ci < 2; ++ci) {
            float cosn = ci == 0 ? 9.0f : 17.0f;
            for (int i = 0; i < 2049; ++i) {
                float t = -0.62f + 1.24f * (float)i / 2048.0f;
                ct[ci * 2049 + i] = (float)std::cos(t * cosn);
            }
        }
        g->cosb = make_buf(g->dev, (UINT)(ct.size() * 4), 4, D3D11_BIND_SHADER_RESOURCE,
                           D3D11_RESOURCE_MISC_BUFFER_STRUCTURED, 0);
        D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
        sd.Format = DXGI_FORMAT_UNKNOWN;
        sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        sd.Buffer.NumElements = (UINT)ct.size();
        g->dev->CreateShaderResourceView(g->cosb, &sd, &g->cosSrv);
        g->ctx->UpdateSubresource(g->cosb, 0, nullptr, ct.data(), 0, 0);
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
    for (auto* v : { g->stampsSrv, g->regionSrv, g->dummySrv, g->tipSrv, g->lutSrv, g->cosSrv,
                     g->fillPtsSrv, g->fillStopsSrv, g->fillMetaSrv, g->fillHolePtsSrv, g->fillHoleOffSrv })
        if (v) v->Release();
    for (auto* b : { g->scratch, g->stamps, g->regionBuf, g->dummy, g->cb, g->lutb, g->tipbuf, g->cosb,
                     g->fillPts, g->fillStops, g->fillMeta, g->fillHolePts, g->fillHoleOff })
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

EXPORT int pg_set_layer_rgba(void* p, int idx, const unsigned char* src) {
    Gpu* g = (Gpu*)p;
    if (!g || !src || idx < 0 || idx >= (int)g->layers.size()) return 0;
    size_t n = (size_t)g->W * g->H;
    std::vector<float> px(n * 4);
    for (size_t i = 0; i < n; ++i) {
        float a = src[i*4+3] / 255.0f;
        px[i*4+0] = src[i*4+0] / 255.0f * a;
        px[i*4+1] = src[i*4+1] / 255.0f * a;
        px[i*4+2] = src[i*4+2] / 255.0f * a;
        px[i*4+3] = a;
    }
    g->ctx->UpdateSubresource(g->layers[idx].buf, 0, nullptr, px.data(), 0, 0);
    return 1;
}

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
    if (g->stamps && g->stampsSrv && g->stamps_cap == n) return;
    if (g->stampsSrv) { g->stampsSrv->Release(); g->stampsSrv = nullptr; }
    if (g->stamps) { g->stamps->Release(); g->stamps = nullptr; }
    UINT cap = n;
    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = cap * 24u * 4u;          // 每个 sprite 24 个 float
    bd.Usage = D3D11_USAGE_DEFAULT;
    bd.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = 4;
    HRESULT hr = g->dev->CreateBuffer(&bd, nullptr, &g->stamps);
    if (FAILED(hr) || !g->stamps) {
        printf("[gpu] stamps buffer failed cap=%u hr=%08lx\n", cap, (unsigned long)hr);
        fflush(stdout);
        g->stamps = nullptr; g->stampsSrv = nullptr; g->stamps_cap = 0;
        return;
    }
    D3D11_SHADER_RESOURCE_VIEW_DESC sd{};
    sd.Format = DXGI_FORMAT_UNKNOWN;
    sd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    sd.Buffer.NumElements = cap * 24u;
    g->dev->CreateShaderResourceView(g->stamps, &sd, &g->stampsSrv);
    g->stamps_cap = cap;
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

// 每笔参数（sprite 记录里逐 stamp 复制，因此一次 dispatch 可以处理整批笔触）
struct StrokeInfo {
    int use_tip, tip_base, tip_w, tip_h;
    int lut_slot, branch, cosn, region_id;
    float kw, tw, tc, thr;
    float opacity, color[3];
};

static void expand_stroke(const float* pts, int n, float size, const StrokeInfo& si,
                          std::vector<float>& flat, unsigned seed) {
    const float baseR = size * 0.5f;
    float total = 0.0f;
    if (n > 1) {
        for (int i = 1; i < n; ++i) {
            float dx = pts[i * 3 + 0] - pts[(i - 1) * 3 + 0];
            float dy = pts[i * 3 + 1] - pts[(i - 1) * 3 + 1];
            total += sqrtf(dx * dx + dy * dy);
        }
    }
    unsigned si_k = 0;
    float arc = 0.0f;
    auto push = [&](float x, float y, float press, float dx, float dy, float tpos) {
        float pr = 0.30f + 0.70f * press;
        float pa = 0.15f + 0.85f * press;
        unsigned k = si_k++;
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
        // a=(x,y,R,alpha) b=(dx,dy,kLocal,0) c=(r,g,b,opacity)
        flat.push_back(x); flat.push_back(y); flat.push_back(baseR * pr); flat.push_back(pa);
        flat.push_back(dx); flat.push_back(dy); flat.push_back((float)k); flat.push_back(0.0f);
        flat.push_back(si.color[0]); flat.push_back(si.color[1]); flat.push_back(si.color[2]); flat.push_back(si.opacity);
        // d=(branch,lut_slot,cosn,kw) e=(tw,tc,thr,region_id) f=(use_tip,tip_base,tip_w,tip_h)
        flat.push_back((float)si.branch); flat.push_back((float)si.lut_slot);
        flat.push_back((float)si.cosn); flat.push_back(si.kw);
        flat.push_back(si.tw); flat.push_back(si.tc); flat.push_back(si.thr); flat.push_back((float)si.region_id);
        flat.push_back((float)si.use_tip); flat.push_back((float)si.tip_base);
        flat.push_back((float)si.tip_w); flat.push_back((float)si.tip_h);
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
    memset(tmp, 0, sizeof(Params));
    memcpy(tmp, &p, sizeof(Params));
    g->ctx->UpdateSubresource(g->cb, 0, nullptr, tmp, 0, 0);
}


static std::vector<float> g_grad_stops;
static int g_grad_n = 0;
static float g_grad_x1 = 0, g_grad_y1 = 0, g_grad_x2 = 1, g_grad_y2 = 0;
static int g_grad_spread = 0;
static int g_grad_mode = 0;
static float g_grad_m[6] = {0,0,0,0,0,0};
static float g_grad_cx = 0, g_grad_cy = 0, g_grad_r = 1, g_grad_fx = 0, g_grad_fy = 0;

EXPORT void pg_set_stroke_gradient(const float* stops, int ns, float x1, float y1, float x2, float y2) {
    g_grad_n = 0;
    if (stops && ns >= 2) {
        g_grad_stops.assign(stops, stops + ns * 5);
        g_grad_n = ns;
        g_grad_x1 = x1; g_grad_y1 = y1; g_grad_x2 = x2; g_grad_y2 = y2;
        g_grad_spread = 0;
    }
}

EXPORT void pg_set_stroke_gradient_spread(const float* stops, int ns, float x1, float y1, float x2, float y2, int spread) {
    pg_set_stroke_gradient(stops, ns, x1, y1, x2, y2);
    g_grad_spread = spread;
    g_grad_mode = 0;
}

EXPORT void pg_set_stroke_gradient_radial(const float* stops, int ns, float cx, float cy, float r, float fx, float fy, int spread) {
    pg_set_stroke_gradient(stops, ns, 0, 0, 1, 0);
    g_grad_mode = 1; g_grad_cx = cx; g_grad_cy = cy; g_grad_r = r; g_grad_fx = fx; g_grad_fy = fy; g_grad_spread = spread;
}

EXPORT void pg_set_stroke_gradient_radial_xform(const float* stops, int ns, float cx, float cy, float r, float fx, float fy,
                                                 float ma, float mb, float mc, float md, float me, float mf, int spread) {
    pg_set_stroke_gradient(stops, ns, 0, 0, 1, 0);
    g_grad_mode = 2; g_grad_cx = cx; g_grad_cy = cy; g_grad_r = r; g_grad_fx = fx; g_grad_fy = fy; g_grad_spread = spread;
    g_grad_m[0]=ma; g_grad_m[1]=mb; g_grad_m[2]=mc; g_grad_m[3]=md; g_grad_m[4]=me; g_grad_m[5]=mf;
}

EXPORT int pg_stroke_batch(void* p, int layer, const float* pts, const int* offs,
                           int n_strokes, const float* params, const int* rids) {
    Gpu* g = (Gpu*)p;
    if (!g || !pts || n_strokes <= 0) return 0;
    g_dyn_scatter = g->dyn_scatter; g_dyn_size = g->dyn_size; g_dyn_alpha = g->dyn_alpha;
    g_dyn_angle = g->dyn_angle; g_dyn_taper = g->dyn_taper; g_color_dyn = g->color_dyn;
    if (layer < 0 || layer >= (int)g->layers.size()) return 0;
    Layer& L = g->layers[layer];
    if (g->gradStops && g->gradMeta) {
        float gm[19] = { g_grad_n >= 2 ? 1.0f : 0.0f, (float)g_grad_n, g_grad_x1, g_grad_y1, g_grad_x2, g_grad_y2,
                         (float)g_grad_spread, (float)g_grad_mode, g_grad_cx, g_grad_cy, g_grad_r, g_grad_fx, g_grad_fy,
                         g_grad_m[0], g_grad_m[1], g_grad_m[2], g_grad_m[3], g_grad_m[4], g_grad_m[5] };
        g->ctx->UpdateSubresource(g->gradMeta, 0, nullptr, gm, 0, 0);
        if (g_grad_n >= 2) g->ctx->UpdateSubresource(g->gradStops, 0, nullptr, g_grad_stops.data(), 0, 0);
    }
    if (g->wet > 0.0f && g->snap) g->ctx->CopyResource(g->snap, L.buf);

    const int CHUNK = 512;
    const int GC = 16;                    // 划格粒度：格内笔触互不重叠才能合并成一次 dispatch
    std::vector<float> tmp;
    std::vector<std::vector<float>> gflat;
    std::vector<int> cells;
    std::vector<int> gb;                  // 每组 union bbox：x0,y0,x1,y1
    std::vector<int> touched;
    double t0 = now_ms();
    for (int c0 = 0; c0 < n_strokes; c0 += CHUNK) {
        int c1 = std::min(n_strokes, c0 + CHUNK);
        int gw = ((int)g->W + GC - 1) / GC, gh = ((int)g->H + GC - 1) / GC;
        cells.assign((size_t)gw * gh, -1);
        gflat.clear();
        gb.clear();
        for (int i = c0; i < c1; ++i) {
            int b = offs[i], e_ = offs[i + 1];
            StrokeInfo si{};
            si.opacity = params[i * 8 + 1];
            si.color[0] = params[i * 8 + 2];
            si.color[1] = params[i * 8 + 3];
            si.color[2] = params[i * 8 + 4];
            int preset = (int)(params[i * 8 + 7] + 0.5f);
            si.use_tip = preset >= 100 ? 1 : 0;
            si.lut_slot = 0; si.branch = 0; si.cosn = 9;
            si.kw = 0.5f; si.tw = 0.55f; si.tc = 0.45f; si.thr = 0.62f;
            si.region_id = rids ? rids[i] : 0;
            if (si.use_tip) {
                int ti = preset - 100;
                if (ti < 0 || ti >= (int)g->tips.size()) si.use_tip = 0;
                else { si.tip_base = g->tips[ti].base; si.tip_w = g->tips[ti].w; si.tip_h = g->tips[ti].h; }
            }
            if (!si.use_tip) {
                si.lut_slot = lut_slot_for(g, preset, params[i * 8 + 5]);
                if (preset == 2) { si.branch = 1; si.cosn = 9; si.kw = 0.42f; si.tw = 0.70f; si.tc = 0.30f; }
                else if (preset == 8) { si.branch = 1; si.cosn = 17; si.kw = 0.50f; si.tw = 0.55f; si.tc = 0.45f; }
                else if (preset == 3) { si.branch = 2; si.thr = 0.42f; }
                else if (preset == 6) { si.branch = 2; si.thr = 0.28f; }
            }
            tmp.clear();
            double t_ex = now_ms();
            expand_stroke(pts + b, (e_ - b) / 3, params[i * 8 + 0], si, tmp, (unsigned)i);
            if (g_grad_n >= 2) {
                float gx = g_grad_x2 - g_grad_x1, gy = g_grad_y2 - g_grad_y1, gl = gx*gx + gy*gy;
                for (size_t q = 0; q * 24 < tmp.size(); ++q) {
                    float sx = tmp[q*24+0], sy = tmp[q*24+1];
                    float t = gl > 1e-9f ? ((sx - g_grad_x1)*gx + (sy - g_grad_y1)*gy) / gl : 0.0f;
                    t = t < 0 ? 0 : (t > 1 ? 1 : t);
                    int k0 = 0; while (k0 + 1 < g_grad_n && g_grad_stops[(k0+1)*5] < t) ++k0;
                    int k1 = std::min(g_grad_n - 1, k0 + 1);
                    float o0 = g_grad_stops[k0*5], o1 = g_grad_stops[k1*5];
                    float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
                    tmp[q*24+8] = g_grad_stops[k0*5+1] + (g_grad_stops[k1*5+1] - g_grad_stops[k0*5+1]) * u;
                    tmp[q*24+9] = g_grad_stops[k0*5+2] + (g_grad_stops[k1*5+2] - g_grad_stops[k0*5+2]) * u;
                    tmp[q*24+10] = g_grad_stops[k0*5+3] + (g_grad_stops[k1*5+3] - g_grad_stops[k0*5+3]) * u;
                }
            }
            g_t_expand += now_ms() - t_ex;
            if (tmp.empty()) continue;
            float ext = (si.use_tip || si.branch != 0) ? 1.3f : 1.0f;
            int x0 = INT_MAX, y0 = INT_MAX, x1 = -1, y1 = -1;
            for (size_t q = 0; q * 24 < tmp.size(); ++q) {
                float sx = tmp[q * 24 + 0], sy = tmp[q * 24 + 1], sr = tmp[q * 24 + 2];
                int qx0 = (int)floorf(sx - sr * ext), qx1 = (int)ceilf(sx + sr * ext);
                int qy0 = (int)floorf(sy - sr * ext), qy1 = (int)ceilf(sy + sr * ext);
                if (qx0 < x0) x0 = qx0;
                if (qx1 > x1) x1 = qx1;
                if (qy0 < y0) y0 = qy0;
                if (qy1 > y1) y1 = qy1;
            }
            x0 = std::max(0, x0); y0 = std::max(0, y0);
            x1 = std::min((int)g->W - 1, x1); y1 = std::min((int)g->H - 1, y1);
            if (x1 < x0 || y1 < y0) continue;
            int cx0 = x0 / GC, cx1 = x1 / GC, cy0 = y0 / GC, cy1 = y1 / GC;
            int gidx = 0;
            for (int cy = cy0; cy <= cy1; ++cy)
                for (int cx = cx0; cx <= cx1; ++cx) {
                    int v = cells[(size_t)cy * gw + cx];
                    if (v + 1 > gidx) gidx = v + 1;
                }
            if ((int)gflat.size() <= gidx) { gflat.resize(gidx + 1); gb.resize((size_t)(gidx + 1) * 4); }
            gflat[gidx].insert(gflat[gidx].end(), tmp.begin(), tmp.end());
            int* bb = &gb[gidx * 4];
            if (gflat[gidx].size() == tmp.size()) { bb[0] = x0; bb[1] = y0; bb[2] = x1; bb[3] = y1; }
            else {
                if (x0 < bb[0]) bb[0] = x0;
                if (y0 < bb[1]) bb[1] = y0;
                if (x1 > bb[2]) bb[2] = x1;
                if (y1 > bb[3]) bb[3] = y1;
            }
            for (int cy = cy0; cy <= cy1; ++cy)
                for (int cx = cx0; cx <= cx1; ++cx) cells[(size_t)cy * gw + cx] = gidx;
        }
        for (size_t gi = 0; gi < gflat.size(); ++gi) {
            std::vector<float>& flat = gflat[gi];
            if (flat.empty()) continue;
            int x0 = gb[gi * 4 + 0], y0 = gb[gi * 4 + 1], x1 = gb[gi * 4 + 2], y1 = gb[gi * 4 + 3];
            UINT total = (UINT)(flat.size() / 24);
            ensure_stamps(g, total);
            if (!g->stamps || !g->stampsSrv) continue;
            double t_up = now_ms();
            g->ctx->UpdateSubresource(g->stamps, 0, nullptr, flat.data(), 0, 0);
            g_t_upload += now_ms() - t_up;
            Params cp{};
            memset(&cp, 0, sizeof(cp));
            cp.W = g->W; cp.H = g->H;
            cp.use_region = g->hasRegion ? 1 : 0;
            cp.nstamps = total;
            cp.stamp_base = 0;
            cp.bx0 = x0; cp.by0 = y0; cp.bx1 = x1; cp.by1 = y1;
            cp.use_color = g->color_dyn > 0.0f ? 1u : 0u;
            cp.color_dyn = g->color_dyn;
            cp.seed = 0u;
            cp.wet = g->wet;
            UINT bs = (total + 255u) / 256u;
            if (bs < 1) bs = 1; if (bs > 1024) bs = 1024;
            cp.th_splat = bs * 256u;
            UINT bo = (UINT)((x1 - x0 + 1) * (y1 - y0 + 1) + 255) / 256u;
            if (bo < 1) bo = 1; if (bo > 1024) bo = 1024;
            cp.th_over = bo * 256u;
            upload_params(g, cp);
            ID3D11ShaderResourceView* srvs[6] = { g->stampsSrv,
                                                  g->hasRegion ? g->regionSrv : g->dummySrv,
                                                  g->tipSrv ? g->tipSrv : g->dummySrv,
                                                  g->lutSrv, g->dummySrv,
                                                  (g->wet > 0.0f && g->snapSrv) ? g->snapSrv : g->dummySrv };
            ID3D11UnorderedAccessView* uavs[2] = { g->scratchUav, L.uav };
            g->ctx->CSSetShaderResources(0, 6, srvs);
            g->ctx->CSSetUnorderedAccessViews(0, 2, uavs, nullptr);
            g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
            g->ctx->CSSetShader(g->cs_splat, nullptr, 0);
            g->ctx->Dispatch(bs, 1, 1);
            ++g_n_disp;
            ID3D11ShaderResourceView* gr[2] = { g->gradStopsSrv, g->gradMetaSrv };
            g->ctx->CSSetShaderResources(11, 2, gr);
            g->ctx->CSSetShader(g->cs_over, nullptr, 0);
            g->ctx->Dispatch(bo, 1, 1);
            ID3D11ShaderResourceView* no[2] = { nullptr, nullptr };
            g->ctx->CSSetShaderResources(11, 2, no);
            ++g_n_disp;
        }
    }
    g_t_splat += now_ms() - t0;
    return 1;
}

EXPORT const char* pg_prof(void) {
    static char buf[256];
    snprintf(buf, sizeof(buf), "expand %.0f upload %.0f splat %.0f over %.0f ms disp %lu",
             g_t_expand, g_t_upload, g_t_splat, g_t_over, g_n_disp);
    return buf;
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




static int gpu_fill_shader(Gpu* g, int idx, const float* pts, int n, const float* stops, int ns,
                           float x1, float y1, float x2, float y2, float a, int use_grad, int spread,
                           const float* hpts = nullptr, int hn = 0,
                           const uint32_t* hoff = nullptr, int nh = 0) {
    if (!g || !pts || n < 3 || idx < 0 || idx >= (int)g->layers.size() || !g->fillPts || !g->fillStops || !g->fillMeta) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    if (ns > 64) return 0;
    if (!ensure_struct(g->dev, g->fillPts, g->fillPtsSrv, g->fillPtsCap, (UINT)n, 8)) return 0;
    g->ctx->UpdateSubresource(g->fillPts, 0, nullptr, pts, 0, 0);
    float one[5] = {0, 0, 0, 0, 1};
    if (!stops || ns < 1) { stops = one; ns = 1; }
    if (!ensure_struct(g->dev, g->fillStops, g->fillStopsSrv, g->fillStopsCap, (UINT)(ns * 5), 4)) return 0;
    g->ctx->UpdateSubresource(g->fillStops, 0, nullptr, stops, 0, 0);
    if (nh > 0) {
        if (!hpts || !hoff) return 0;
        if (!ensure_struct(g->dev, g->fillHolePts, g->fillHolePtsSrv, g->fillHolePtsCap, (UINT)hn, 8)) return 0;
        if (!ensure_struct(g->dev, g->fillHoleOff, g->fillHoleOffSrv, g->fillHoleOffCap, (UINT)(nh + 1), 4)) return 0;
        g->ctx->UpdateSubresource(g->fillHolePts, 0, nullptr, hpts, 0, 0);
        g->ctx->UpdateSubresource(g->fillHoleOff, 0, nullptr, hoff, 0, 0);
    }
    float meta[24] = {(float)n, (float)ns, (float)use_grad, x1, y1, x2, y2, a, (float)spread, 0,
                      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, (float)nh, 0, 0};
    g->ctx->UpdateSubresource(g->fillMeta, 0, nullptr, meta, 0, 0);
    Params cp{}; cp.W = g->W; cp.H = g->H;
    cp.color[0] = stops[1]; cp.color[1] = stops[2]; cp.color[2] = stops[3]; cp.color[3] = 1.0f;
    cp.opacity = a;
    g->ctx->UpdateSubresource(g->cb, 0, nullptr, &cp, 0, 0);
    ID3D11UnorderedAccessView* uav = g->layers[idx].uav;
    ID3D11ShaderResourceView* srvs[5] = { g->fillPtsSrv, g->fillStopsSrv, g->fillMetaSrv,
                                          g->fillHolePtsSrv, g->fillHoleOffSrv };
    g->ctx->CSSetShader(g->cs_fill, nullptr, 0);
    g->ctx->CSSetUnorderedAccessViews(1, 1, &uav, nullptr);
    g->ctx->CSSetShaderResources(8, 5, srvs);
    g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
    g->ctx->Dispatch((npix + 255) / 256, 1, 1);
    ID3D11UnorderedAccessView* none = nullptr;
    g->ctx->CSSetUnorderedAccessViews(1, 1, &none, nullptr);
    ID3D11ShaderResourceView* no[5] = { nullptr, nullptr, nullptr, nullptr, nullptr };
    g->ctx->CSSetShaderResources(8, 5, no);
    g->ctx->CSSetShader(nullptr, nullptr, 0);
    return 1;
}

static int gpu_fill_common(Gpu* g, int idx, const float* pts, int n, const float* stops, int ns,
                           float x1, float y1, float x2, float y2, float a, int use_grad) {
    if (!g || !pts || n < 3 || idx < 0 || idx >= (int)g->layers.size()) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    std::vector<float> px((size_t)npix * 4);
    ID3D11Buffer* rb = make_buf(g->dev, npix * 16, 16, 0, 0, D3D11_CPU_ACCESS_READ);
    g->ctx->CopyResource(rb, g->layers[idx].buf);
    D3D11_MAPPED_SUBRESOURCE ms{};
    if (FAILED(g->ctx->Map(rb, 0, D3D11_MAP_READ, 0, &ms))) { rb->Release(); return 0; }
    memcpy(px.data(), ms.pData, (size_t)npix * 16);
    g->ctx->Unmap(rb, 0);
    rb->Release();
    float gx = x2 - x1, gy = y2 - y1, gl = gx*gx + gy*gy;
    for (int y = 0; y < g->H; ++y) {
        std::vector<float> xs;
        float yy = y + 0.5f;
        for (int i = 0; i < n; ++i) {
            int j = (i + 1) % n;
            float ax = pts[i*2], ay = pts[i*2+1], bx = pts[j*2], by = pts[j*2+1];
            if ((ay <= yy && by > yy) || (by <= yy && ay > yy))
                xs.push_back(ax + (yy - ay) * (bx - ax) / (by - ay));
        }
        std::sort(xs.begin(), xs.end());
        for (size_t k = 0; k + 1 < xs.size(); k += 2) {
            int xa = std::max(0, (int)std::ceil(xs[k] - 0.5f));
            int xb = std::min(g->W - 1, (int)std::floor(xs[k+1] - 0.5f));
            for (int x = xa; x <= xb; ++x) {
                float cr = 0, cg = 0, cb = 0, ca = 1;
                if (use_grad && ns >= 2) {
                    float t = gl > 1e-9f ? ((x + 0.5f - x1)*gx + (y + 0.5f - y1)*gy) / gl : 0.0f;
                    t = t < 0 ? 0 : (t > 1 ? 1 : t);
                    int k0 = 0; while (k0 + 1 < ns && stops[(k0+1)*5] < t) ++k0;
                    int k1 = std::min(ns - 1, k0 + 1);
                    float o0 = stops[k0*5], o1 = stops[k1*5];
                    float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
                    cr = stops[k0*5+1] + (stops[k1*5+1] - stops[k0*5+1]) * u;
                    cg = stops[k0*5+2] + (stops[k1*5+2] - stops[k0*5+2]) * u;
                    cb = stops[k0*5+3] + (stops[k1*5+3] - stops[k0*5+3]) * u;
                    ca = stops[k0*5+4] + (stops[k1*5+4] - stops[k0*5+4]) * u;
                } else if (stops) { cr = stops[1]; cg = stops[2]; cb = stops[3]; }
                float sa = a * ca;
                if (sa <= 0) continue;
                float* d = px.data() + ((size_t)y * g->W + x) * 4;
                float inv = 1.0f - sa;
                d[0] = cr * sa + d[0] * inv;
                d[1] = cg * sa + d[1] * inv;
                d[2] = cb * sa + d[2] * inv;
                d[3] = sa + d[3] * inv;
            }
        }
    }
    g->ctx->UpdateSubresource(g->layers[idx].buf, 0, nullptr, px.data(), 0, 0);
    return 1;
}

EXPORT int pg_fill_polygon(void* p, int idx, const float* pts, int n, float r, float g_, float b, float a) {
    float one[5] = {0, r, g_, b, 1};
    return gpu_fill_shader((Gpu*)p, idx, pts, n, one, 2, 0, 0, 1, 0, a, 0, 0);
}

EXPORT int pg_fill_polygon_holes(void* p, int idx, const float* pts, int n,
                                  const float* holes, int hole_n,
                                  const int* hole_off, int nh,
                                  float r, float g_, float b, float a) {
    float one[5] = {0, r, g_, b, 1};
    return gpu_fill_shader((Gpu*)p, idx, pts, n, one, 2, 0, 0, 1, 0, a, 0, 0,
                           holes, hole_n, (const uint32_t*)hole_off, nh);
}

EXPORT int pg_fill_radial_polygon_spread(void* p, int idx, const float* pts, int n,
                                            const float* stops, int ns, float cx, float cy, float r,
                                            float fx, float fy, float a, int spread) {
    Gpu* g = (Gpu*)p;
    if (!g || !pts || n < 3 || idx < 0 || idx >= (int)g->layers.size() || !g->fillMeta) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    if (ns > 64) return 0;
    if (!ensure_struct(g->dev, g->fillPts, g->fillPtsSrv, g->fillPtsCap, (UINT)n, 8)) return 0;
    if (!ensure_struct(g->dev, g->fillStops, g->fillStopsSrv, g->fillStopsCap, (UINT)(ns * 5), 4)) return 0;
    g->ctx->UpdateSubresource(g->fillPts, 0, nullptr, pts, 0, 0);
    g->ctx->UpdateSubresource(g->fillStops, 0, nullptr, stops, 0, 0);
    float meta[21] = {(float)n, (float)ns, 1.0f, 0, 0, 0, 0, a, (float)spread, 1.0f, cx, cy, r, fx, fy,
                      0, 0, 0, 0, 0, 0};
    g->ctx->UpdateSubresource(g->fillMeta, 0, nullptr, meta, 0, 0);
    Params cp{}; cp.W = g->W; cp.H = g->H;
    cp.color[0] = stops[1]; cp.color[1] = stops[2]; cp.color[2] = stops[3]; cp.color[3] = 1.0f;
    cp.opacity = a;
    g->ctx->UpdateSubresource(g->cb, 0, nullptr, &cp, 0, 0);
    ID3D11UnorderedAccessView* uav = g->layers[idx].uav;
    ID3D11ShaderResourceView* srvs[3] = { g->fillPtsSrv, g->fillStopsSrv, g->fillMetaSrv };
    g->ctx->CSSetShader(g->cs_fill, nullptr, 0);
    g->ctx->CSSetUnorderedAccessViews(1, 1, &uav, nullptr);
    g->ctx->CSSetShaderResources(8, 3, srvs);
    g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
    g->ctx->Dispatch((npix + 255) / 256, 1, 1);
    ID3D11UnorderedAccessView* none = nullptr;
    g->ctx->CSSetUnorderedAccessViews(1, 1, &none, nullptr);
    ID3D11ShaderResourceView* no[3] = { nullptr, nullptr, nullptr };
    g->ctx->CSSetShaderResources(8, 3, no);
    g->ctx->CSSetShader(nullptr, nullptr, 0);
    return 1;
}

EXPORT int pg_fill_radial_xform(void* p, int idx, const float* pts, int n,
                                  const float* stops, int ns, float cx, float cy, float r,
                                  float fx, float fy, float ma, float mb, float mc, float md,
                                  float me, float mf, float a, int spread) {
    Gpu* g = (Gpu*)p;
    if (!g || !pts || n < 3 || idx < 0 || idx >= (int)g->layers.size() || !g->fillMeta) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    if (ns > 64) return 0;
    if (!ensure_struct(g->dev, g->fillPts, g->fillPtsSrv, g->fillPtsCap, (UINT)n, 8)) return 0;
    if (!ensure_struct(g->dev, g->fillStops, g->fillStopsSrv, g->fillStopsCap, (UINT)(ns * 5), 4)) return 0;
    g->ctx->UpdateSubresource(g->fillPts, 0, nullptr, pts, 0, 0);
    g->ctx->UpdateSubresource(g->fillStops, 0, nullptr, stops, 0, 0);
    float meta[21] = {(float)n, (float)ns, 1.0f, 0, 0, 0, 0, a, (float)spread, 2.0f,
                      cx, cy, r, fx, fy, ma, mb, mc, md, me, mf};
    g->ctx->UpdateSubresource(g->fillMeta, 0, nullptr, meta, 0, 0);
    Params cp{}; cp.W = g->W; cp.H = g->H;
    cp.color[0] = stops[1]; cp.color[1] = stops[2]; cp.color[2] = stops[3]; cp.color[3] = 1.0f;
    cp.opacity = a;
    g->ctx->UpdateSubresource(g->cb, 0, nullptr, &cp, 0, 0);
    ID3D11UnorderedAccessView* uav = g->layers[idx].uav;
    ID3D11ShaderResourceView* srvs[3] = { g->fillPtsSrv, g->fillStopsSrv, g->fillMetaSrv };
    g->ctx->CSSetShader(g->cs_fill, nullptr, 0);
    g->ctx->CSSetUnorderedAccessViews(1, 1, &uav, nullptr);
    g->ctx->CSSetShaderResources(8, 3, srvs);
    g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
    g->ctx->Dispatch((npix + 255) / 256, 1, 1);
    ID3D11UnorderedAccessView* none = nullptr;
    g->ctx->CSSetUnorderedAccessViews(1, 1, &none, nullptr);
    ID3D11ShaderResourceView* no[3] = { nullptr, nullptr, nullptr };
    g->ctx->CSSetShaderResources(8, 3, no);
    g->ctx->CSSetShader(nullptr, nullptr, 0);
    return 1;
}

EXPORT int pg_fill_gradient_polygon(void* p, int idx, const float* pts, int n,
                                    const float* stops, int ns, float x1, float y1, float x2, float y2, float a) {
    return gpu_fill_shader((Gpu*)p, idx, pts, n, stops, ns, x1, y1, x2, y2, a, 1, 0);
}

EXPORT int pg_fill_gradient_polygon_spread(void* p, int idx, const float* pts, int n,
                                           const float* stops, int ns, float x1, float y1, float x2, float y2, float a, int spread) {
    return gpu_fill_shader((Gpu*)p, idx, pts, n, stops, ns, x1, y1, x2, y2, a, 1, spread);
}

EXPORT int pg_apply_layer_mask(void* p, int idx, const unsigned char* mask, int mw, int mh) {
    Gpu* g = (Gpu*)p;
    if (!g || !mask || idx < 0 || idx >= (int)g->layers.size() || mw <= 0 || mh <= 0) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    std::vector<float> alpha((size_t)npix);
    for (int y = 0; y < g->H; ++y) {
        int my = (int)((long long)y * mh / g->H);
        if (my >= mh) my = mh - 1;
        for (int x = 0; x < g->W; ++x) {
            int mx = (int)((long long)x * mw / g->W);
            if (mx >= mw) mx = mw - 1;
            alpha[(size_t)y * g->W + x] = mask[(size_t)my * mw + mx] / 255.0f;
        }
    }
    g->ctx->UpdateSubresource(g->maskBuf, 0, nullptr, alpha.data(), 0, 0);
    Params cp{};
    cp.W = g->W; cp.H = g->H;
    g->ctx->UpdateSubresource(g->cb, 0, nullptr, &cp, 0, 0);
    ID3D11UnorderedAccessView* uav = g->layers[idx].uav;
    ID3D11ShaderResourceView* srv = g->maskSrv;
    g->ctx->CSSetShader(g->cs_mask, nullptr, 0);
    g->ctx->CSSetUnorderedAccessViews(1, 1, &uav, nullptr);
    g->ctx->CSSetShaderResources(7, 1, &srv);
    g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
    g->ctx->Dispatch((npix + 255) / 256, 1, 1);
    ID3D11UnorderedAccessView* none = nullptr;
    g->ctx->CSSetUnorderedAccessViews(1, 1, &none, nullptr);
    ID3D11ShaderResourceView* no = nullptr;
    g->ctx->CSSetShaderResources(7, 1, &no);
    g->ctx->CSSetShader(nullptr, nullptr, 0);
    return 1;
}


EXPORT int pg_apply_layer_mask_rect(void* p, int idx, const unsigned char* mask, int mw, int mh,
                                    int x0, int y0, int x1, int y1) {
    Gpu* g = (Gpu*)p;
    if (!g || !mask || idx < 0 || idx >= (int)g->layers.size() || mw <= 0 || mh <= 0) return 0;
    UINT npix = (UINT)g->W * (UINT)g->H;
    std::vector<float> alpha((size_t)npix);
    for (int y = 0; y < g->H; ++y) {
        int my = (int)((long long)y * mh / g->H); if (my >= mh) my = mh - 1;
        for (int x = 0; x < g->W; ++x) {
            int mx = (int)((long long)x * mw / g->W); if (mx >= mw) mx = mw - 1;
            alpha[(size_t)y * g->W + x] = mask[(size_t)my * mw + mx] / 255.0f;
        }
    }
    g->ctx->UpdateSubresource(g->maskBuf, 0, nullptr, alpha.data(), 0, 0);
    Params cp{}; cp.W = g->W; cp.H = g->H;
    cp.bx0 = std::max(0, x0); cp.by0 = std::max(0, y0);
    cp.bx1 = std::min(g->W - 1, x1); cp.by1 = std::min(g->H - 1, y1);
    g->ctx->UpdateSubresource(g->cb, 0, nullptr, &cp, 0, 0);
    ID3D11UnorderedAccessView* uav = g->layers[idx].uav;
    ID3D11ShaderResourceView* srv = g->maskSrv;
    g->ctx->CSSetShader(g->cs_mask, nullptr, 0);
    g->ctx->CSSetUnorderedAccessViews(1, 1, &uav, nullptr);
    g->ctx->CSSetShaderResources(7, 1, &srv);
    g->ctx->CSSetConstantBuffers(0, 1, &g->cb);
    g->ctx->Dispatch((npix + 255) / 256, 1, 1);
    ID3D11UnorderedAccessView* none = nullptr;
    g->ctx->CSSetUnorderedAccessViews(1, 1, &none, nullptr);
    ID3D11ShaderResourceView* no = nullptr;
    g->ctx->CSSetShaderResources(7, 1, &no);
    g->ctx->CSSetShader(nullptr, nullptr, 0);
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
