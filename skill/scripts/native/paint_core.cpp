// paint_core.cpp - 笔触光栅器 v3：多笔头 / 多图层 / 压力校准表 / 顺序等价的多线程批渲染
#include <cmath>
#include <cstring>
#include <climits>
#include <cstdint>
#include <vector>
#include <algorithm>
#include <thread>
#include <atomic>

#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C"
#endif

namespace {

enum Preset { P_ROUND_SOFT = 0, P_ROUND_HARD = 1, P_FLAT = 2, P_DRY = 3, P_AIRBRUSH = 4,
              P_LINER = 5, P_CHALK = 6, P_SPONGE = 7, P_BRISTLE = 8, P_MARKER = 9 };
enum Blend { B_NORMAL = 0, B_MULTIPLY = 1, B_SCREEN = 2,
             B_OVERLAY = 3, B_SOFTLIGHT = 4, B_DODGE = 5, B_SUBTRACT = 6, B_ADD = 7,
             B_DARKEN = 8, B_LIGHTEN = 9, B_COLORBURN = 10, B_HARDLIGHT = 11,
             B_VIVIDLIGHT = 12, B_LINEARLIGHT = 13, B_PINLIGHT = 14,
             B_DIFFERENCE = 15, B_EXCLUSION = 16, B_DIVIDE = 17, B_LINEARBURN = 18,
             B_GRAINEXTRACT = 19, B_GRAINMERGE = 20,
             B_HARDMIX = 21, B_HUE = 22, B_SATURATION = 23, B_COLOR = 24, B_LUMINOSITY = 25 };

inline float clamp01(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }

// HSL 辅助（色相/饱和度/明度模式用）
inline void rgb2hsl(float r, float g, float b, float& h, float& s, float& l) {
    float mx = std::max(r, std::max(g, b)), mn = std::min(r, std::min(g, b));
    l = (mx + mn) * 0.5f;
    float d = mx - mn;
    if (d < 1e-6f) { h = s = 0.0f; return; }
    s = l > 0.5f ? d / (2.0f - mx - mn) : d / (mx + mn);
    if (mx == r) h = (g - b) / d + (g < b ? 6.0f : 0.0f);
    else if (mx == g) h = (b - r) / d + 2.0f;
    else h = (r - g) / d + 4.0f;
    h /= 6.0f;
}

inline float hue2rgb(float p, float q, float t) {
    if (t < 0.0f) t += 1.0f;
    if (t > 1.0f) t -= 1.0f;
    if (t < 1.0f / 6.0f) return p + (q - p) * 6.0f * t;
    if (t < 0.5f) return q;
    if (t < 2.0f / 3.0f) return p + (q - p) * (2.0f / 3.0f - t) * 6.0f;
    return p;
}

inline void hsl2rgb(float h, float s, float l, float& r, float& g, float& b) {
    if (s < 1e-6f) { r = g = b = l; return; }
    float q = l < 0.5f ? l * (1.0f + s) : l + s - l * s;
    float p = 2.0f * l - q;
    r = hue2rgb(p, q, h + 1.0f / 3.0f);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1.0f / 3.0f);
}

// 混合模式：通道值 0..1，src 为上层、dst 为下层
inline void blend_px(int mode, float sr, float sg, float sb, float dr, float dg, float db,
                     float& br, float& bg, float& bb) {
    switch (mode) {
        case B_MULTIPLY: br = sr * dr; bg = sg * dg; bb = sb * db; break;
        case B_SCREEN: br = sr + dr - sr * dr; bg = sg + dg - sg * dg; bb = sb + db - sb * db; break;
        case B_OVERLAY:
            br = dr < 0.5f ? 2.0f * sr * dr : 1.0f - 2.0f * (1.0f - sr) * (1.0f - dr);
            bg = dg < 0.5f ? 2.0f * sg * dg : 1.0f - 2.0f * (1.0f - sg) * (1.0f - dg);
            bb = db < 0.5f ? 2.0f * sb * db : 1.0f - 2.0f * (1.0f - sb) * (1.0f - db);
            break;
        case B_SOFTLIGHT: {
            auto sl = [](float s, float d) {
                if (s <= 0.5f) return d - (1.0f - 2.0f * s) * d * (1.0f - d);
                float dd = d <= 0.25f ? ((16.0f * d - 12.0f) * d + 4.0f) * d : std::sqrt(d);
                return d + (2.0f * s - 1.0f) * (dd - d);
            };
            br = sl(sr, dr); bg = sl(sg, dg); bb = sl(sb, db);
            break;
        }
        case B_DODGE:
            br = dr >= 1.0f ? 1.0f : clamp01(sr / (1.0f - dr));
            bg = dg >= 1.0f ? 1.0f : clamp01(sg / (1.0f - dg));
            bb = db >= 1.0f ? 1.0f : clamp01(sb / (1.0f - db));
            break;
        case B_SUBTRACT: br = dr - sr; bg = dg - sg; bb = db - sb; break;
        case B_ADD: br = sr + dr; bg = sg + dg; bb = sb + db; break;
        case B_DARKEN: br = std::min(sr, dr); bg = std::min(sg, dg); bb = std::min(sb, db); break;
        case B_LIGHTEN: br = std::max(sr, dr); bg = std::max(sg, dg); bb = std::max(sb, db); break;
        case B_COLORBURN: {
            auto cb = [](float s, float d) {
                if (d <= 0.0f) return 0.0f;
                if (s >= 1.0f) return d;
                return 1.0f - std::min(1.0f, (1.0f - d) / s);
            };
            br = cb(sr, dr); bg = cb(sg, dg); bb = cb(sb, db);
            break;
        }
        case B_HARDLIGHT: {
            auto hl = [](float s, float d) {
                return s <= 0.5f ? 2.0f * s * d : 1.0f - 2.0f * (1.0f - s) * (1.0f - d);
            };
            br = hl(sr, dr); bg = hl(sg, dg); bb = hl(sb, db);
            break;
        }
        case B_VIVIDLIGHT: {
            auto vl = [](float s, float d) {
                if (s <= 0.5f) {
                    float t = 1.0f - 2.0f * s;
                    return t >= 1.0f ? 0.0f : std::max(0.0f, 1.0f - (1.0f - d) / t);
                }
                float t = 2.0f * s - 1.0f;
                return std::min(1.0f, d / std::max(1e-6f, 1.0f - t));
            };
            br = vl(sr, dr); bg = vl(sg, dg); bb = vl(sb, db);
            break;
        }
        case B_LINEARLIGHT: {
            auto ll = [](float s, float d) { return d + 2.0f * s - 1.0f; };
            br = ll(sr, dr); bg = ll(sg, dg); bb = ll(sb, db);
            break;
        }
        case B_PINLIGHT: {
            auto pl = [](float s, float d) {
                return s <= 0.5f ? std::min(d, 2.0f * s) : std::max(d, 2.0f * s - 1.0f);
            };
            br = pl(sr, dr); bg = pl(sg, dg); bb = pl(sb, db);
            break;
        }
        case B_DIFFERENCE: br = std::fabs(sr - dr); bg = std::fabs(sg - dg); bb = std::fabs(sb - db); break;
        case B_EXCLUSION: br = sr + dr - 2.0f * sr * dr; bg = sg + dg - 2.0f * sg * dg; bb = sb + db - 2.0f * sb * db; break;
        case B_DIVIDE:
            br = dr <= 0.0f ? 1.0f : clamp01(sr / dr);
            bg = dg <= 0.0f ? 1.0f : clamp01(sg / dg);
            bb = db <= 0.0f ? 1.0f : clamp01(sb / db);
            break;
        case B_LINEARBURN: br = sr + dr - 1.0f; bg = sg + dg - 1.0f; bb = sb + db - 1.0f; break;
        case B_GRAINEXTRACT: br = clamp01(dr - sr + 0.5f); bg = clamp01(dg - sg + 0.5f); bb = clamp01(db - sb + 0.5f); break;
        case B_GRAINMERGE: br = clamp01(dr + sr - 0.5f); bg = clamp01(dg + sg - 0.5f); bb = clamp01(db + sb - 0.5f); break;
        case B_HARDMIX: br = sr + dr >= 1.0f ? 1.0f : 0.0f; bg = sg + dg >= 1.0f ? 1.0f : 0.0f; bb = sb + db >= 1.0f ? 1.0f : 0.0f; break;
        case B_HUE: case B_SATURATION: case B_COLOR: case B_LUMINOSITY: {
            float sh, ss, sl, dh, ds, dl;
            rgb2hsl(sr, sg, sb, sh, ss, sl);
            rgb2hsl(dr, dg, db, dh, ds, dl);
            float hh = dh, hs = ds, hl = dl;
            if (mode == B_HUE) hh = sh, hs = ds, hl = dl;   // 原先漏了 hh=sh，等于把上层色画成下层色（no-op）
            else if (mode == B_SATURATION) { hh = dh; hl = dl; hs = ss; }
            else if (mode == B_COLOR) { hh = sh; hs = ss; hl = dl; }
            else { hh = dh; hs = ds; hl = sl; }
            hsl2rgb(hh, hs, hl, br, bg, bb);
            break;
        }
        default: br = sr; bg = sg; bb = sb; break;
    }
    br = clamp01(br); bg = clamp01(bg); bb = clamp01(bb);
}

struct Tip2D {
    int w = 0, h = 0;
    std::vector<float> a;
};

struct Layer {
    std::vector<float> px;
    int blend = B_NORMAL;
};

// 笔刷动力学：全局参数，0 = 完全关闭（与旧行为逐位一致）
static float g_dyn_scatter = 0.0f, g_dyn_size = 0.0f, g_dyn_alpha = 0.0f, g_dyn_angle = 0.0f, g_dyn_taper = 0.0f;

struct Ctx {
    int w = 0, h = 0;
    std::vector<Layer> layers;
    std::vector<float> scratch1;   // 单笔路径复用的全画布临时缓冲
    std::vector<uint32_t> kb1;     // 单笔路径：每像素获胜 stamp 序号
    float color_dyn = 0.0f;        // 逐 stamp 色彩微差强度（按画布存，避免全局串台）
    std::vector<Tip2D> tips;       // 位图笔尖（追加式，不改公式预设）
    float wet = 0.0f;              // 湿混合取色比例：0 = 关闭（与旧行为逐位一致）
    std::vector<float> snap;       // 每批调用开始时的图层快照（取色来源，只读）
};

struct Stamp {
    float x, y, R, a, dx, dy;
    uint32_t k;          // stamp 序号（逐 stamp 上色用）
};

inline float smoothstep(float e0, float e1, float x) {
    if (e0 == e1) return x >= e1 ? 1.0f : 0.0f;
    float t = (x - e0) / (e1 - e0);
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return t * t * (3.0f - 2.0f * t);
}

inline float hash2i(uint32_t x, uint32_t y, uint32_t s) {
    uint32_t h = x * 374761393u + y * 668265263u + s * 2246822519u;
    h = (h ^ (h >> 13)) * 1274126177u;
    h ^= h >> 16;
    return (float)(h & 0xFFFFFF) / 16777216.0f;
}

// 逐 stamp 上色：记录每像素「获胜」的 stamp 序号，合成时按序号做确定性色彩微差
static float g_dyn_color = 0.0f;
static thread_local uint32_t* g_kbuf = nullptr;
static thread_local uint32_t g_kcur = 0u;

inline void put_k(float* base, const float* cell) {
    if (g_kbuf) g_kbuf[(size_t)(cell - base)] = g_kcur;
}

inline void jitter_color(float& cr, float& cg, float& cb, float r, float g, float b,
                         uint32_t seed, uint32_t k) {
    float m = 1.0f + g_dyn_color * (hash2i(seed, k, 16u) * 2.0f - 1.0f);
    float tw = g_dyn_color * 0.25f * (hash2i(seed, k, 17u) * 2.0f - 1.0f);
    cr = r * m * (1.0f + tw);
    cg = g * m;
    cb = b * m * (1.0f - tw);
    if (cr < 0.0f) cr = 0.0f; else if (cr > 1.0f) cr = 1.0f;
    if (cg < 0.0f) cg = 0.0f; else if (cg > 1.0f) cg = 1.0f;
    if (cb < 0.0f) cb = 0.0f; else if (cb > 1.0f) cb = 1.0f;
}

inline void build_tip_table(float* tbl, float hardness, int preset) {
    for (int k = 0; k < 256; ++k) {
        float t = (float)k / 255.0f;
        float d = std::sqrt(t);
        float v;
        switch (preset) {
            case P_ROUND_HARD: v = d <= 0.94f ? 1.0f : 1.0f - smoothstep(0.94f, 1.0f, d); break;
            case P_AIRBRUSH: { float b = 1.0f - smoothstep(0.15f, 1.0f, d); v = b * b * 0.5f; break; }
            case P_LINER: v = d <= 0.975f ? 1.0f : 1.0f - smoothstep(0.975f, 1.0f, d); break;
            case P_CHALK: v = 1.0f - smoothstep(0.30f, 1.0f, d); break;
            case P_SPONGE: v = d <= 0.80f ? 1.0f : 1.0f - smoothstep(0.80f, 1.0f, d); break;
            case P_BRISTLE: v = 1.0f - smoothstep(0.45f, 1.0f, d); break;
            case P_MARKER: v = d <= 0.90f ? 0.98f : 0.98f * (1.0f - smoothstep(0.90f, 1.0f, d)); break;
            case P_DRY: v = 1.0f - smoothstep(hardness, 1.0f, d); break;
            default: {
                float core = hardness;
                v = d <= core ? 1.0f : 1.0f - smoothstep(core, 1.0f, d);
            }
        }
        tbl[k] = v;
    }
}

static thread_local float g_flat_lut[2049];
static thread_local float g_gro9[2049];
static thread_local float g_gro17[2049];
static thread_local bool g_lut_ready = false;

inline void ensure_luts() {
    if (g_lut_ready) return;
    for (int i = 0; i <= 2048; ++i) {
        float e2 = (float)i / 2048.0f;
        float e = std::sqrt(e2);
        g_flat_lut[i] = 1.0f - smoothstep(0.70f, 1.0f, e);
        float t = -0.62f + 1.24f * (float)i / 2048.0f;
        g_gro9[i] = std::cos(t * 9.0f);
        g_gro17[i] = std::cos(t * 17.0f);
    }
    g_lut_ready = true;
}

// 把一个 stamp 打到 scratch 上；只处理 [ry0, ry1) 行（scratch 原点为 ry0）
// rx0/rx1：每行实际写入的列跨度，合成时只扫这段
// 分支按行判定（tip2 / 径向 / 平笔 / 干笔），避免逐像素判断
inline void stamp_band(float* scratch, int w, int ry0, int ry1, float cx, float cy, float R,
                       float a, int preset, float dirx, float diry, uint32_t seed,
                       const float* tip_tbl, float invR2,
                       const int* region, int region_id,
                       int& tby0, int& tby1, const Tip2D* tip2,
                       int* rx0, int* rx1) {
    int x0 = (int)std::floor(cx - R * 1.3f), x1 = (int)std::ceil(cx + R * 1.3f);
    int y0 = std::max(ry0, (int)std::floor(cy - R * 1.3f));
    int y1 = std::min(ry1 - 1, (int)std::ceil(cy + R * 1.3f));
    if (x0 < 0) x0 = 0;
    if (x1 > w - 1) x1 = w - 1;
    if (x0 > x1 || y0 > y1) return;
    if (y0 < tby0) tby0 = y0;
    if (y1 > tby1) tby1 = y1;
    const float R2 = R * R * 1.69f;
    const float tipInv = (tip2 ? 1.0f / (R * 2.0f) : 0.0f);
    const bool radial = (!tip2) && (preset == P_ROUND_SOFT || preset == P_ROUND_HARD ||
                                    preset == P_AIRBRUSH || preset == P_LINER ||
                                    preset == P_SPONGE || preset == P_MARKER);
    const bool flatish = (!tip2) && (preset == P_FLAT || preset == P_BRISTLE);
    const float kw = (preset == P_FLAT) ? 0.42f : 0.50f;
    const float tw = (preset == P_FLAT) ? 0.70f : 0.55f;
    const float tc = (preset == P_FLAT) ? 0.30f : 0.45f;
    const float thr = (preset == P_DRY) ? 0.42f : (preset == P_CHALK ? 0.28f : 0.62f);
    for (int y = y0; y <= y1; ++y) {
        float dy = (float)y + 0.5f - cy;
        float rem = R2 - dy * dy;
        if (rem <= 0.0f) continue;
        float half = std::sqrt(rem);
        int xs = (int)std::ceil(cx - half - 0.5f);
        int xe = (int)std::floor(cx + half - 0.5f);
        if (xs < x0) xs = x0;
        if (xe > x1) xe = x1;
        if (xs > xe) continue;
        float* row = scratch + (size_t)(y - ry0) * w;
        int span0 = INT_MAX, span1 = -1;
        const int* regrow = region ? region + (size_t)y * w : nullptr;
        if (tip2) {
            const int tw1 = tip2->w - 1, th1 = tip2->h - 1;
            const int tw = tip2->w;
            float* row2 = scratch + (size_t)(y - ry0) * w;
            int s0 = INT_MAX, s1 = -1;
            float dx0 = (float)xs + 0.5f - cx;
            float u = (dx0 * dirx + dy * diry) * tipInv + 0.5f;
            float v = (-dx0 * diry + dy * dirx) * tipInv + 0.5f;
            const float du = dirx * tipInv, dv = -diry * tipInv;
            for (int x = xs; x <= xe; ++x, u += du, v += dv) {
                if (regrow) {
                    int rv = regrow[x];
                    if (rv != region_id && rv != -2) continue;
                }
                if (u < -2e-4f || u > 1.0002f || v < -2e-4f || v > 1.0002f) continue;
                float ru = u * tw1 + 0.5f, rv = v * th1 + 0.5f;
                float fu = ru - (float)(int)ru, fv = rv - (float)(int)rv;
                if (u < 2e-4f || u > 0.9998f || v < 2e-4f || v > 0.9998f ||
                    fu < 2e-4f || fu > 0.9998f || fv < 2e-4f || fv > 0.9998f) {
                    float dxr = (float)x + 0.5f - cx;
                    u = (dxr * dirx + dy * diry) * tipInv + 0.5f;
                    v = (-dxr * diry + dy * dirx) * tipInv + 0.5f;
                    if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) continue;
                    ru = u * tw1 + 0.5f;
                    rv = v * th1 + 0.5f;
                }
                int ix = (int)ru;
                int iy = (int)rv;
                float av = tip2->a[(size_t)iy * tw + ix] * a;
                if (av > row2[x]) row2[x] = av;
                if (x < s0) s0 = x;
                if (x > s1) s1 = x;
            }
            if (s1 >= s0 && rx0) {
                int i2 = y - ry0;
                if (s0 < rx0[i2]) rx0[i2] = s0;
                if (s1 > rx1[i2]) rx1[i2] = s1;
            }
            continue;
        }
        for (int x = xs; x <= xe; ++x) {
            if (regrow) {
                int rv = regrow[x];
                if (rv != region_id && rv != -2) continue;
            }
            float dx = (float)x + 0.5f - cx;
            float av;
            if (tip2) {
                float u = (dx * dirx + dy * diry) * tipInv + 0.5f;
                float v = (-dx * diry + dy * dirx) * tipInv + 0.5f;
                if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) continue;
                int ix = (int)(u * (tip2->w - 1) + 0.5f);
                int iy = (int)(v * (tip2->h - 1) + 0.5f);
                av = tip2->a[(size_t)iy * tip2->w + ix] * a;
            } else if (radial) {
                float d2 = dx * dx + dy * dy;
                int k = (int)(d2 * invR2 * 255.0f);
                if (k > 255) k = 255;
                av = tip_tbl[k] * a;
            } else if (flatish) {
                float along = dx * dirx + dy * diry;
                float perp = -dx * diry + dy * dirx;
                float aa = along / (R * 1.25f), bb = perp / (R * kw);
                float e2 = aa * aa + bb * bb;
                if (e2 >= 1.0f) continue;
                float base;
                if (e2 <= 0.49f) {
                    base = 1.0f;
                } else {
                    float e = std::sqrt(e2);
                    base = 1.0f - smoothstep(0.70f, 1.0f, e);
                }
                float gi_f = (perp / R + 0.62f) * (2048.0f / 1.24f);
                int gi = (int)gi_f;
                if (gi < 0) gi = 0;
                if (gi > 2047) gi = 2047;
                float fr = gi_f - (float)gi;
                if (fr < 0.0f) fr = 0.0f;
                if (fr > 1.0f) fr = 1.0f;
                const float* gt = (preset == P_FLAT) ? g_gro9 : g_gro17;
                float gro = gt[gi] + (gt[gi + 1] - gt[gi]) * fr;
                av = base * (tw + tc * gro) * a;
            } else {
                float d = std::sqrt(dx * dx + dy * dy);
                float rnd = hash2i((uint32_t)x, (uint32_t)y, seed);
                av = rnd > thr ? tip_tbl[(int)std::min(255.0f, d / R * 255.0f)] * a : 0.0f;
            }
            if (av > row[x]) row[x] = av;
            if (x < span0) span0 = x;
            if (x > span1) span1 = x;
        }
        if (span1 >= span0 && rx0) {
            int i2 = y - ry0;
            if (span0 < rx0[i2]) rx0[i2] = span0;
            if (span1 > rx1[i2]) rx1[i2] = span1;
        }
    }
}


// 包围盒局部缓存 stamp（与整幅版逐位等价）
inline void stamp_bbox(float* scratch, int sw, int sx0, int sy0, int sx1, int sy1,
                       float cx, float cy, float R, float a, int preset, float dirx, float diry,
                       uint32_t seed, const float* tip_tbl, float invR2,
                       const int* region, int W, int region_id, const Tip2D* tip2) {
    int x0 = (int)std::floor(cx - R * 1.3f), x1 = (int)std::ceil(cx + R * 1.3f);
    int y0 = (int)std::floor(cy - R * 1.3f), y1 = (int)std::ceil(cy + R * 1.3f);
    if (x0 < sx0) x0 = sx0;
    if (x1 > sx1) x1 = sx1;
    if (y0 < sy0) y0 = sy0;
    if (y1 > sy1) y1 = sy1;
    if (x0 > x1 || y0 > y1) return;
    const float R2 = R * R * 1.69f;
    const float tipInv = (tip2 ? 1.0f / (R * 2.0f) : 0.0f);
    const bool radial = (!tip2) && (preset == P_ROUND_SOFT || preset == P_ROUND_HARD ||
                                    preset == P_AIRBRUSH || preset == P_LINER ||
                                    preset == P_SPONGE || preset == P_MARKER);
    const bool flatish = (!tip2) && (preset == P_FLAT || preset == P_BRISTLE);
    const float kw = (preset == P_FLAT) ? 0.42f : 0.50f;
    const float tw = (preset == P_FLAT) ? 0.70f : 0.55f;
    const float tc = (preset == P_FLAT) ? 0.30f : 0.45f;
    const float thr = (preset == P_DRY) ? 0.42f : (preset == P_CHALK ? 0.28f : 0.62f);
    for (int y = y0; y <= y1; ++y) {
        float dy = (float)y + 0.5f - cy;
        float rem = R2 - dy * dy;
        if (rem <= 0.0f) continue;
        float half = std::sqrt(rem);
        int xs = (int)std::ceil(cx - half - 0.5f);
        int xe = (int)std::floor(cx + half - 0.5f);
        if (xs < x0) xs = x0;
        if (xe > x1) xe = x1;
        if (xs > xe) continue;
        const int* regrow = region ? region + (size_t)y * W : nullptr;
        if (tip2) {
            const int tw1 = tip2->w - 1, th1 = tip2->h - 1;
            const int tw = tip2->w;
            float* cell = scratch + (size_t)(y - sy0) * sw + (xs - sx0);
            float dx0 = (float)xs + 0.5f - cx;
            float u = (dx0 * dirx + dy * diry) * tipInv + 0.5f;
            float v = (-dx0 * diry + dy * dirx) * tipInv + 0.5f;
            const float du = dirx * tipInv, dv = -diry * tipInv;
            for (int x = xs; x <= xe; ++x, ++cell, u += du, v += dv) {
                if (regrow) {
                    int rv = regrow[x];
                    if (rv != region_id && rv != -2) continue;
                }
                if (u < -2e-4f || u > 1.0002f || v < -2e-4f || v > 1.0002f) continue;
                float ru = u * tw1 + 0.5f, rv = v * th1 + 0.5f;
                float fu = ru - (float)(int)ru, fv = rv - (float)(int)rv;
                if (u < 2e-4f || u > 0.9998f || v < 2e-4f || v > 0.9998f ||
                    fu < 2e-4f || fu > 0.9998f || fv < 2e-4f || fv > 0.9998f) {
                    float dxr = (float)x + 0.5f - cx;
                    u = (dxr * dirx + dy * diry) * tipInv + 0.5f;
                    v = (-dxr * diry + dy * dirx) * tipInv + 0.5f;
                    if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) continue;
                    ru = u * tw1 + 0.5f;
                    rv = v * th1 + 0.5f;
                }
                int ix = (int)ru;
                int iy = (int)rv;
                float av = tip2->a[(size_t)iy * tw + ix] * a;
                if (av > *cell) { *cell = av; put_k(scratch, cell); }
            }
            continue;
        }
        float* cell = scratch + (size_t)(y - sy0) * sw + (xs - sx0);
        for (int x = xs; x <= xe; ++x, ++cell) {
            if (regrow) {
                int rv = regrow[x];
                if (rv != region_id && rv != -2) continue;
            }
            float dx = (float)x + 0.5f - cx;
            float av;
            if (tip2) {
                float u = (dx * dirx + dy * diry) * tipInv + 0.5f;
                float v = (-dx * diry + dy * dirx) * tipInv + 0.5f;
                if (u < 0.0f || u > 1.0f || v < 0.0f || v > 1.0f) continue;
                int ix = (int)(u * (tip2->w - 1) + 0.5f);
                int iy = (int)(v * (tip2->h - 1) + 0.5f);
                av = tip2->a[(size_t)iy * tip2->w + ix] * a;
            } else if (radial) {
                float d2 = dx * dx + dy * dy;
                int k = (int)(d2 * invR2 * 255.0f);
                if (k > 255) k = 255;
                av = tip_tbl[k] * a;
            } else if (flatish) {
                float along = dx * dirx + dy * diry;
                float perp = -dx * diry + dy * dirx;
                float aa = along / (R * 1.25f), bb = perp / (R * kw);
                float e2 = aa * aa + bb * bb;
                if (e2 >= 1.0f) continue;
                // 内部（e2<=0.49）：smoothstep 恒为 0，衰减精确等于 1
                // 软边一圈才走精确 sqrt + smoothstep，保证与无 LUT 版逐位一致
                float base;
                if (e2 <= 0.49f) {
                    base = 1.0f;
                } else {
                    float e = std::sqrt(e2);
                    base = 1.0f - smoothstep(0.70f, 1.0f, e);
                }
                float gi_f = (perp / R + 0.62f) * (2048.0f / 1.24f);
                int gi = (int)gi_f;
                if (gi < 0) gi = 0;
                if (gi > 2047) gi = 2047;
                float fr = gi_f - (float)gi;
                if (fr < 0.0f) fr = 0.0f;
                if (fr > 1.0f) fr = 1.0f;
                const float* gt = (preset == P_FLAT) ? g_gro9 : g_gro17;
                float gro = gt[gi] + (gt[gi + 1] - gt[gi]) * fr;
                av = base * (tw + tc * gro) * a;
            } else {
                float d = std::sqrt(dx * dx + dy * dy);
                float rnd = hash2i((uint32_t)x, (uint32_t)y, seed);
                av = rnd > thr ? tip_tbl[(int)std::min(255.0f, d / R * 255.0f)] * a : 0.0f;
            }
            if (av > *cell) { *cell = av; put_k(scratch, cell); }
        }
    }
}

// 一笔：先算 stamp 序列，再按行带落到 scratch，最后 alpha-over 到图层
inline const Tip2D* resolve_tip2(Ctx* c, int preset) {
    int k = preset - 100;
    if (k >= 0 && k < (int)c->tips.size()) return &c->tips[k];
    return nullptr;
}

inline void render_stroke_band(Ctx* c, int layer, const Stamp* st, int nst,
                               float opacity, float r, float g, float b,
                               int preset, const float* tip_tbl,
                               int ry0, int ry1) {
    const Tip2D* tip2 = resolve_tip2(c, preset);
    std::vector<float> scratch((size_t)(ry1 - ry0) * c->w, 0.0f);
    std::vector<int> rx0(ry1 - ry0, INT_MAX), rx1(ry1 - ry0, -1);
    int tby0 = ry1, tby1 = ry0 - 1;
    for (int i = 0; i < nst; ++i) {
        const Stamp& s = st[i];
        if (s.R <= 0.0f) continue;
        stamp_band(scratch.data(), c->w, ry0, ry1, s.x, s.y, s.R, s.a, preset, s.dx, s.dy,
                   (uint32_t)(layer * 7919 + i), tip_tbl, 1.0f / (s.R * s.R),
                   nullptr, 0, tby0, tby1, tip2, rx0.data(), rx1.data());
    }
    if (tby1 < tby0) return;
    Layer& L = c->layers[layer];
    for (int y = tby0; y <= tby1; ++y) {
        float* dst = L.px.data() + (size_t)y * c->w * 4;
        float* scr = scratch.data() + (size_t)(y - ry0) * c->w;
        for (int x = 0; x < c->w; ++x) {
            float sa = scr[x] * opacity;
            if (sa <= 0.0f) continue;
            float* d = dst + (size_t)x * 4;
            float inv = 1.0f - sa;
            d[0] = r * sa + d[0] * inv;
            d[1] = g * sa + d[1] * inv;
            d[2] = b * sa + d[2] * inv;
            d[3] = sa + d[3] * inv;
            scr[x] = 0.0f;
        }
    }
}

inline void make_stamps(std::vector<Stamp>& out, const float* pts, int n, float size,
                        const float* lut_r, const float* lut_a, int lut_n, unsigned seed) {
    out.clear();
    const float baseR = size * 0.5f;
    float total = 0.0f;
    if (n > 1) {
        for (int i = 1; i < n; ++i) {
            float dx = pts[i * 3 + 0] - pts[(i - 1) * 3 + 0];
            float dy = pts[i * 3 + 1] - pts[(i - 1) * 3 + 1];
            total += std::sqrt(dx * dx + dy * dy);
        }
    }
    unsigned si = 0;
    float arc = 0.0f;
    auto push = [&](float x, float y, float press, float dx, float dy, float tpos) {
        float pr = 0.30f + 0.70f * press;
        float pa = 0.15f + 0.85f * press;
        if (lut_r && lut_a && lut_n > 1) {
            float fp = press * (lut_n - 1);
            if (fp < 0) fp = 0;
            if (fp > lut_n - 1) fp = (float)(lut_n - 1);
            int i0 = (int)fp;
            int i1 = i0 + 1 < lut_n ? i0 + 1 : i0;
            float f = fp - (float)i0;
            pr = lut_r[i0] * (1.0f - f) + lut_r[i1] * f;
            pa = lut_a[i0] * (1.0f - f) + lut_a[i1] * f;
        }
        unsigned k = si++;
        if (g_dyn_taper > 0.0f && total > 1e-6f) {
            float f0 = tpos / g_dyn_taper, f1 = (1.0f - tpos) / g_dyn_taper;
            if (f0 > 1.0f) f0 = 1.0f;
            if (f1 > 1.0f) f1 = 1.0f;
            float f = smoothstep(0.0f, 1.0f, f0) * smoothstep(0.0f, 1.0f, f1);
            pr *= 0.35f + 0.65f * f;
        }
        if (g_dyn_size > 0.0f) pr *= 1.0f + g_dyn_size * (hash2i(seed, k, 13u) * 2.0f - 1.0f);
        if (g_dyn_alpha > 0.0f) pa *= 1.0f + g_dyn_alpha * (hash2i(seed, k, 14u) * 2.0f - 1.0f);
        if (g_dyn_scatter > 0.0f) {
            float ang = hash2i(seed, k, 11u) * 6.2831853f;
            float rad = hash2i(seed, k, 12u) * g_dyn_scatter * baseR * pr;
            x += std::cos(ang) * rad;
            y += std::sin(ang) * rad;
        }
        if (g_dyn_angle > 0.0f) {
            float aa = g_dyn_angle * (hash2i(seed, k, 15u) * 2.0f - 1.0f);
            float ca = std::cos(aa), sa = std::sin(aa);
            float nx = dx * ca - dy * sa, ny = dx * sa + dy * ca;
            dx = nx; dy = ny;
        }
        if (pr < 0.0f) pr = 0.0f;
        if (pa < 0.0f) pa = 0.0f;
        if (pa > 1.0f) pa = 1.0f;
        out.push_back({x, y, baseR * pr, pa, dx, dy, k});
    };
    if (n == 1) {
        push(pts[0], pts[1], pts[2], 1.0f, 0.0f, 0.0f);
    } else {
        for (int i = 1; i < n; ++i) {
            float x0 = pts[(i - 1) * 3 + 0], y0 = pts[(i - 1) * 3 + 1], p0 = pts[(i - 1) * 3 + 2];
            float x1 = pts[i * 3 + 0], y1 = pts[i * 3 + 1], p1 = pts[i * 3 + 2];
            float dx = x1 - x0, dy = y1 - y0;
            float len = std::sqrt(dx * dx + dy * dy);
            float ux = len > 1e-6f ? dx / len : 1.0f, uy = len > 1e-6f ? dy / len : 0.0f;
            float step = std::max(0.6f, baseR * 0.30f);
            int steps = (int)std::floor(len / step);
            if (steps < 1) steps = 1;
            for (int s = 0; s <= steps; ++s) {
                float t = (float)s / (float)steps;
                float ap = total > 1e-6f ? (arc + len * t) / total : 0.0f;
                push(x0 + dx * t, y0 + dy * t, p0 + (p1 - p0) * t, ux, uy, ap);
            }
            arc += len;
        }
    }
}

} // namespace

EXPORT void* paint_create(int w, int h) {
    if (w <= 0 || h <= 0) return nullptr;
    Ctx* c = new Ctx();
    c->w = w; c->h = h;
    c->layers.emplace_back();
    c->layers[0].px.assign((size_t)w * h * 4, 0.0f);
    c->scratch1.assign((size_t)w * h, 0.0f);
    return c;
}

EXPORT void paint_destroy(void* p) { delete (Ctx*)p; }

EXPORT int paint_layer_count(void* p) { return p ? (int)((Ctx*)p)->layers.size() : 0; }

EXPORT int paint_add_layer(void* p, int blend) {
    Ctx* c = (Ctx*)p;
    if (!c) return -1;
    Layer l;
    l.px.assign((size_t)c->w * c->h * 4, 0.0f);
    l.blend = blend;
    c->layers.push_back(std::move(l));
    return (int)c->layers.size() - 1;
}

EXPORT void paint_clear_rect(void* p, int idx, int x0, int y0, int x1, int y1) {
    Ctx* c = (Ctx*)p;
    if (!c || idx < 0 || idx >= (int)c->layers.size()) return;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > c->w - 1) x1 = c->w - 1;
    if (y1 > c->h - 1) y1 = c->h - 1;
    if (x1 < x0 || y1 < y0) return;
    std::vector<float>& px = c->layers[idx].px;
    for (int y = y0; y <= y1; ++y) {
        float* row = px.data() + (size_t)y * c->w * 4;
        for (int x = x0; x <= x1; ++x) {
            float* d = row + (size_t)x * 4;
            d[0] = d[1] = d[2] = d[3] = 0.0f;
        }
    }
}

EXPORT void paint_set_wet(void* p, float amount) {
    Ctx* c = (Ctx*)p;
    if (c) c->wet = amount;
}

EXPORT void paint_set_color_dynamics(void* p, float amount) {
    Ctx* c = (Ctx*)p;
    if (c) c->color_dyn = amount > 0.0f ? amount : 0.0f;
}

EXPORT void paint_set_dynamics(void* p, float scatter, float size_jit, float alpha_jit, float angle_jit, float taper) {
    (void)p;
    g_dyn_scatter = scatter; g_dyn_size = size_jit; g_dyn_alpha = alpha_jit;
    g_dyn_angle = angle_jit; g_dyn_taper = taper;
}

EXPORT void paint_clear_layer(void* p, int idx, float r, float g, float b, float a) {
    Ctx* c = (Ctx*)p;
    if (!c || idx < 0 || idx >= (int)c->layers.size()) return;
    std::vector<float>& px = c->layers[idx].px;
    size_t n = (size_t)c->w * c->h;
    for (size_t i = 0; i < n; ++i) {
        px[i * 4 + 0] = r * a; px[i * 4 + 1] = g * a; px[i * 4 + 2] = b * a; px[i * 4 + 3] = a;
    }
}





EXPORT int paint_apply_layer_mask_rect(void* p, int layer, const unsigned char* mask, int mw, int mh,
                                       int x0, int y0, int x1, int y1) {
    Ctx* c = (Ctx*)p;
    if (!c || !mask || mw <= 0 || mh <= 0) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    Layer& L = c->layers[layer];
    int xa = std::max(0, x0), ya = std::max(0, y0);
    int xb = std::min(c->w - 1, x1), yb = std::min(c->h - 1, y1);
    for (int y = ya; y <= yb; ++y) {
        int my = (int)((long long)y * mh / c->h);
        if (my >= mh) my = mh - 1;
        for (int x = xa; x <= xb; ++x) {
            int mx = (int)((long long)x * mw / c->w);
            if (mx >= mw) mx = mw - 1;
            float a = mask[(size_t)my * mw + mx] / 255.0f;
            float* d = L.px.data() + ((size_t)y * c->w + x) * 4;
            d[0] *= a; d[1] *= a; d[2] *= a; d[3] *= a;
        }
    }
    return 1;
}

EXPORT int paint_apply_layer_mask(void* p, int layer, const unsigned char* mask, int mw, int mh) {
    Ctx* c = (Ctx*)p;
    if (!c || !mask || mw <= 0 || mh <= 0) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    Layer& L = c->layers[layer];
    for (int y = 0; y < c->h; ++y) {
        int my = (int)((long long)y * mh / c->h);
        if (my >= mh) my = mh - 1;
        for (int x = 0; x < c->w; ++x) {
            int mx = (int)((long long)x * mw / c->w);
            if (mx >= mw) mx = mw - 1;
            float a = mask[(size_t)my * mw + mx] / 255.0f;
            float* d = L.px.data() + ((size_t)y * c->w + x) * 4;
            d[0] *= a; d[1] *= a; d[2] *= a; d[3] *= a;
        }
    }
    return 1;
}


static inline float grad_spread(float t, int mode) {
    if (mode == 1) {
        t = std::fabs(t);
        t = t - std::floor(t * 0.5f) * 2.0f;
        if (t > 1.0f) t = 2.0f - t;
        return t;
    }
    if (mode == 2) return t - std::floor(t);
    return t < 0 ? 0 : (t > 1 ? 1 : t);
}

static int stroke_gradient_impl(void* p, int layer, const float* pts, int n,
                                   float size, float opacity,
                                   const float* stops, int ns,
                                   float gx1, float gy1, float gx2, float gy2,
                                   int preset, float hardness, int spread) {
    Ctx* c = (Ctx*)p;
    if (!c || !pts || n < 1 || !stops || ns < 2) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    float tbl[256]; build_tip_table(tbl, hardness, preset);
    std::vector<Stamp> st; st.reserve(512);
    make_stamps(st, pts, n, size, nullptr, nullptr, 0, 0u);
    std::vector<float>& scratch = c->scratch1;
    if (scratch.size() < (size_t)c->w * c->h) scratch.assign((size_t)c->w * c->h, 0.0f);
    else std::fill(scratch.begin(), scratch.end(), 0.0f);
    const Tip2D* tip2 = resolve_tip2(c, preset);
    for (auto& s : st) {
        int t0 = 0, t1 = c->h - 1;
        stamp_band(scratch.data(), c->w, 0, c->h, s.x, s.y, s.R, s.a, preset, s.dx, s.dy,
                   0, tbl, 1.0f / (s.R * s.R), nullptr, 0, t0, t1, tip2, nullptr, nullptr);
    }
    Layer& L = c->layers[layer];
    float gx = gx2 - gx1, gy = gy2 - gy1, gl = gx*gx + gy*gy;
    for (int y = 0; y < c->h; ++y) {
        for (int x = 0; x < c->w; ++x) {
            float sa = scratch[(size_t)y * c->w + x] * opacity;
            if (sa <= 0.0f) continue;
            float t = gl > 1e-9f ? ((x + 0.5f - gx1)*gx + (y + 0.5f - gy1)*gy) / gl : 0.0f;
            t = grad_spread(t, spread);
            int k0 = 0; while (k0 + 1 < ns && stops[(k0+1)*5] < t) ++k0;
            int k1 = std::min(ns - 1, k0 + 1);
            float o0 = stops[k0*5], o1 = stops[k1*5];
            float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
            float cr = stops[k0*5+1] + (stops[k1*5+1] - stops[k0*5+1]) * u;
            float cg = stops[k0*5+2] + (stops[k1*5+2] - stops[k0*5+2]) * u;
            float cb = stops[k0*5+3] + (stops[k1*5+3] - stops[k0*5+3]) * u;
            float ca = stops[k0*5+4] + (stops[k1*5+4] - stops[k0*5+4]) * u;
            sa *= ca;
            if (sa <= 0) continue;
            float* d = L.px.data() + ((size_t)y * c->w + x) * 4;
            float inv = 1.0f - sa;
            d[0] = cr * sa + d[0] * inv;
            d[1] = cg * sa + d[1] * inv;
            d[2] = cb * sa + d[2] * inv;
            d[3] = sa + d[3] * inv;
        }
    }
    return 1;
}

static inline float radial_t(float x, float y, float cx, float cy, float r, float fx, float fy) {
    r = std::fabs(r) < 1e-6f ? 1e-6f : r;
    float dx = x - fx, dy = y - fy;
    float Dx = cx - fx, Dy = cy - fy;
    float A = Dx*Dx + Dy*Dy - r*r;
    float B = -2.0f * (dx*Dx + dy*Dy);
    float C = dx*dx + dy*dy;
    float t;
    if (std::fabs(A) < 1e-9f) t = std::fabs(B) < 1e-12f ? 0.0f : -C / B;
    else {
        float disc = B*B - 4.0f*A*C;
        if (disc < 0.0f) disc = 0.0f;
        t = (-B - std::sqrt(disc)) / (2.0f * A);
    }
    return t;
}

static int stroke_radial_impl(void* p, int layer, const float* pts, int n,
                               float size, float opacity, const float* stops, int ns,
                               float cx, float cy, float r, float fx, float fy,
                               int preset, float hardness, int spread) {
    Ctx* c = (Ctx*)p;
    if (!c || !pts || n < 1 || !stops || ns < 2) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    float tbl[256]; build_tip_table(tbl, hardness, preset);
    std::vector<Stamp> st; st.reserve(512);
    make_stamps(st, pts, n, size, nullptr, nullptr, 0, 0u);
    std::vector<float>& scratch = c->scratch1;
    if (scratch.size() < (size_t)c->w * c->h) scratch.assign((size_t)c->w * c->h, 0.0f);
    else std::fill(scratch.begin(), scratch.end(), 0.0f);
    const Tip2D* tip2 = resolve_tip2(c, preset);
    for (auto& s : st) {
        int t0 = 0, t1 = c->h - 1;
        stamp_band(scratch.data(), c->w, 0, c->h, s.x, s.y, s.R, s.a, preset, s.dx, s.dy,
                   0, tbl, 1.0f / (s.R * s.R), nullptr, 0, t0, t1, tip2, nullptr, nullptr);
    }
    Layer& L = c->layers[layer];
    for (int y = 0; y < c->h; ++y) {
        for (int x = 0; x < c->w; ++x) {
            float sa = scratch[(size_t)y * c->w + x] * opacity;
            if (sa <= 0.0f) continue;
            float t = radial_t(x + 0.5f, y + 0.5f, cx, cy, r, fx, fy);
            t = grad_spread(t, spread);
            int k0 = 0; while (k0 + 1 < ns && stops[(k0+1)*5] < t) ++k0;
            int k1 = std::min(ns - 1, k0 + 1);
            float o0 = stops[k0*5], o1 = stops[k1*5];
            float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
            float cr = stops[k0*5+1] + (stops[k1*5+1] - stops[k0*5+1]) * u;
            float cg = stops[k0*5+2] + (stops[k1*5+2] - stops[k0*5+2]) * u;
            float cb = stops[k0*5+3] + (stops[k1*5+3] - stops[k0*5+3]) * u;
            float ca = stops[k0*5+4] + (stops[k1*5+4] - stops[k0*5+4]) * u;
            sa *= ca;
            if (sa <= 0) continue;
            float* d = L.px.data() + ((size_t)y * c->w + x) * 4;
            float inv = 1.0f - sa;
            d[0] = cr * sa + d[0] * inv; d[1] = cg * sa + d[1] * inv;
            d[2] = cb * sa + d[2] * inv; d[3] = sa + d[3] * inv;
        }
    }
    return 1;
}

EXPORT int paint_stroke_radial_spread(void* p, int layer, const float* pts, int n,
                                      float size, float opacity, const float* stops, int ns,
                                      float cx, float cy, float r, float fx, float fy,
                                      int preset, float hardness, int spread) {
    return stroke_radial_impl(p, layer, pts, n, size, opacity, stops, ns, cx, cy, r, fx, fy, preset, hardness, spread);
}

static int fill_gradient_impl(void* p, int layer, const float* pts, int n,
                                       const float* stops, int ns,
                                       float gx1, float gy1, float gx2, float gy2, float a, int spread) {
    Ctx* c = (Ctx*)p;
    if (!c || !pts || n < 3 || !stops || ns < 2) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    float al = a < 0 ? 0 : (a > 1 ? 1 : a);
    if (al <= 0) return 0;
    Layer& L = c->layers[layer];
    float ymin = 1e30f, ymax = -1e30f;
    for (int i = 0; i < n; ++i) { float y = pts[i*2+1]; ymin = std::min(ymin, y); ymax = std::max(ymax, y); }
    int y0 = std::max(0, (int)std::floor(ymin));
    int y1 = std::min(c->h - 1, (int)std::ceil(ymax));
    float gx = gx2 - gx1, gy = gy2 - gy1, gl = gx*gx + gy*gy;
    std::vector<float> xs;
    for (int y = y0; y <= y1; ++y) {
        float yy = y + 0.5f; xs.clear();
        for (int i = 0; i < n; ++i) {
            int j = (i + 1) % n;
            float ax = pts[i*2], ay = pts[i*2+1];
            float bx = pts[j*2], by = pts[j*2+1];
            if ((ay <= yy && by > yy) || (by <= yy && ay > yy))
                xs.push_back(ax + (yy - ay) * (bx - ax) / (by - ay));
        }
        std::sort(xs.begin(), xs.end());
        for (size_t k = 0; k + 1 < xs.size(); k += 2) {
            int xa = std::max(0, (int)std::ceil(xs[k] - 0.5f));
            int xb = std::min(c->w - 1, (int)std::floor(xs[k+1] - 0.5f));
            for (int x = xa; x <= xb; ++x) {
                float t = gl > 1e-9f ? ((x + 0.5f - gx1)*gx + (y + 0.5f - gy1)*gy) / gl : 0.0f;
                t = grad_spread(t, spread);
                int k0 = 0; while (k0 + 1 < ns && stops[(k0+1)*5] < t) ++k0;
                int k1 = std::min(ns - 1, k0 + 1);
                float o0 = stops[k0*5], o1 = stops[k1*5];
                float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
                float cr = stops[k0*5+1] + (stops[k1*5+1] - stops[k0*5+1]) * u;
                float cg = stops[k0*5+2] + (stops[k1*5+2] - stops[k0*5+2]) * u;
                float cb = stops[k0*5+3] + (stops[k1*5+3] - stops[k0*5+3]) * u;
                float ca = stops[k0*5+4] + (stops[k1*5+4] - stops[k0*5+4]) * u;
                float sa = al * ca;
                if (sa <= 0) continue;
                float* d = L.px.data() + ((size_t)y * c->w + x) * 4;
                float inv = 1.0f - sa;
                d[0] = cr * sa + d[0] * inv;
                d[1] = cg * sa + d[1] * inv;
                d[2] = cb * sa + d[2] * inv;
                d[3] = sa + d[3] * inv;
            }
        }
    }
    return 1;
}

EXPORT int paint_stroke_gradient_l(void* p, int layer, const float* pts, int n,
                                   float size, float opacity, const float* stops, int ns,
                                   float gx1, float gy1, float gx2, float gy2, int preset, float hardness) {
    return stroke_gradient_impl(p, layer, pts, n, size, opacity, stops, ns, gx1, gy1, gx2, gy2, preset, hardness, 0);
}
EXPORT int paint_stroke_gradient_spread(void* p, int layer, const float* pts, int n,
                                        float size, float opacity, const float* stops, int ns,
                                        float gx1, float gy1, float gx2, float gy2, int preset, float hardness, int spread) {
    return stroke_gradient_impl(p, layer, pts, n, size, opacity, stops, ns, gx1, gy1, gx2, gy2, preset, hardness, spread);
}
EXPORT int paint_fill_radial_polygon_spread(void* p, int layer, const float* pts, int n,
                                             const float* stops, int ns,
                                             float cx, float cy, float r, float fx, float fy, float a, int spread) {
    Ctx* c = (Ctx*)p;
    if (!c || !pts || n < 3 || !stops || ns < 2) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    float al = a < 0 ? 0 : (a > 1 ? 1 : a);
    if (al <= 0) return 0;
    Layer& L = c->layers[layer];
    float ymin = 1e30f, ymax = -1e30f;
    for (int i = 0; i < n; ++i) { float y = pts[i*2+1]; ymin = std::min(ymin, y); ymax = std::max(ymax, y); }
    int y0 = std::max(0, (int)std::floor(ymin));
    int y1 = std::min(c->h - 1, (int)std::ceil(ymax));
    std::vector<float> xs;
    for (int y = y0; y <= y1; ++y) {
        float yy = y + 0.5f; xs.clear();
        for (int i = 0; i < n; ++i) {
            int j = (i + 1) % n;
            float ax = pts[i*2], ay = pts[i*2+1], bx = pts[j*2], by = pts[j*2+1];
            if ((ay <= yy && by > yy) || (by <= yy && ay > yy))
                xs.push_back(ax + (yy - ay) * (bx - ax) / (by - ay));
        }
        std::sort(xs.begin(), xs.end());
        for (size_t k = 0; k + 1 < xs.size(); k += 2) {
            int xa = std::max(0, (int)std::ceil(xs[k] - 0.5f));
            int xb = std::min(c->w - 1, (int)std::floor(xs[k+1] - 0.5f));
            for (int x = xa; x <= xb; ++x) {
                float t = radial_t(x + 0.5f, y + 0.5f, cx, cy, r, fx, fy);
                t = grad_spread(t, spread);
                int k0 = 0; while (k0 + 1 < ns && stops[(k0+1)*5] < t) ++k0;
                int k1 = std::min(ns - 1, k0 + 1);
                float o0 = stops[k0*5], o1 = stops[k1*5];
                float u = o1 > o0 ? (t - o0) / (o1 - o0) : 0.0f;
                float cr = stops[k0*5+1] + (stops[k1*5+1] - stops[k0*5+1]) * u;
                float cg = stops[k0*5+2] + (stops[k1*5+2] - stops[k0*5+2]) * u;
                float cb = stops[k0*5+3] + (stops[k1*5+3] - stops[k0*5+3]) * u;
                float ca = stops[k0*5+4] + (stops[k1*5+4] - stops[k0*5+4]) * u;
                float sa = al * ca;
                if (sa <= 0) continue;
                float* d = L.px.data() + ((size_t)y * c->w + x) * 4;
                float inv = 1.0f - sa;
                d[0] = cr * sa + d[0] * inv; d[1] = cg * sa + d[1] * inv;
                d[2] = cb * sa + d[2] * inv; d[3] = sa + d[3] * inv;
            }
        }
    }
    return 1;
}

EXPORT int paint_fill_gradient_polygon(void* p, int layer, const float* pts, int n,
                                       const float* stops, int ns,
                                       float gx1, float gy1, float gx2, float gy2, float a) {
    return fill_gradient_impl(p, layer, pts, n, stops, ns, gx1, gy1, gx2, gy2, a, 0);
}
EXPORT int paint_fill_gradient_polygon_spread(void* p, int layer, const float* pts, int n,
                                              const float* stops, int ns,
                                              float gx1, float gy1, float gx2, float gy2, float a, int spread) {
    return fill_gradient_impl(p, layer, pts, n, stops, ns, gx1, gy1, gx2, gy2, a, spread);
}

EXPORT int paint_fill_polygon(void* p, int layer, const float* pts, int n,
                              float r, float g, float b, float a) {
    Ctx* c = (Ctx*)p;
    if (!c || !pts || n < 3) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    float al = a < 0 ? 0 : (a > 1 ? 1 : a);
    if (al <= 0) return 0;
    Layer& L = c->layers[layer];
    float ymin = 1e30f, ymax = -1e30f;
    for (int i = 0; i < n; ++i) { float y = pts[i*2+1]; ymin = std::min(ymin, y); ymax = std::max(ymax, y); }
    int y0 = std::max(0, (int)std::floor(ymin));
    int y1 = std::min(c->h - 1, (int)std::ceil(ymax));
    std::vector<float> xs;
    for (int y = y0; y <= y1; ++y) {
        float yy = y + 0.5f; xs.clear();
        for (int i = 0; i < n; ++i) {
            int j = (i + 1) % n;
            float ax = pts[i*2], ay = pts[i*2+1];
            float bx = pts[j*2], by = pts[j*2+1];
            if ((ay <= yy && by > yy) || (by <= yy && ay > yy))
                xs.push_back(ax + (yy - ay) * (bx - ax) / (by - ay));
        }
        std::sort(xs.begin(), xs.end());
        for (size_t k = 0; k + 1 < xs.size(); k += 2) {
            int xa = std::max(0, (int)std::ceil(xs[k] - 0.5f));
            int xb = std::min(c->w - 1, (int)std::floor(xs[k+1] - 0.5f));
            for (int x = xa; x <= xb; ++x) {
                float* d = L.px.data() + ((size_t)y * c->w + x) * 4;
                float inv = 1.0f - al;
                d[0] = r * al + d[0] * inv;
                d[1] = g * al + d[1] * inv;
                d[2] = b * al + d[2] * inv;
                d[3] = al + d[3] * inv;
            }
        }
    }
    return 1;
}

EXPORT int paint_stroke_l(void* p, int layer, const float* pts, int n,
                          float size, float opacity, float r, float g, float b,
                          int preset, float hardness, float spacing,
                          const int* region, int region_id,
                          const float* lut_r, const float* lut_a, int lut_n) {
    Ctx* c = (Ctx*)p;
    g_dyn_color = c ? c->color_dyn : 0.0f;
    if (!c || !pts || n < 1) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    float tbl[256];
    build_tip_table(tbl, hardness, preset);
    const Tip2D* tip2 = resolve_tip2(c, preset);
    std::vector<Stamp> st;
    st.reserve(512);
    make_stamps(st, pts, n, size, lut_r, lut_a, lut_n, 0u);

    // 单笔路径：区域裁剪版（复用 scratch1，只清触碰行）
    std::vector<float>& scratch = c->scratch1;
    const bool useCol = g_dyn_color > 0.0f;
    if (useCol) {
        if (c->kb1.size() < (size_t)c->w * c->h) c->kb1.assign((size_t)c->w * c->h, 0u);
        g_kbuf = c->kb1.data();
    } else {
        g_kbuf = nullptr;
    }
    int by0 = c->h, by1 = -1;
    for (auto& s : st) {
        int y0 = std::max(0, (int)std::floor(s.y - s.R * 1.3f));
        int y1 = std::min(c->h - 1, (int)std::ceil(s.y + s.R * 1.3f));
        if (y0 > y1) continue;
        if (y0 < by0) by0 = y0;
        if (y1 > by1) by1 = y1;
    }
    if (by1 < by0) return 0;
    for (auto& s : st) {
        int t0 = by1 + 1, t1 = by0 - 1;
        g_kcur = s.k;
        stamp_band(scratch.data(), c->w, 0, c->h, s.x, s.y, s.R, s.a, preset, s.dx, s.dy,
                   0, tbl, 1.0f / (s.R * s.R), region, region_id, t0, t1, tip2, nullptr, nullptr);
    }
    Layer& L = c->layers[layer];
    for (int y = by0; y <= by1; ++y) {
        float* dst = L.px.data() + (size_t)y * c->w * 4;
        float* scr = scratch.data() + (size_t)y * c->w;
        for (int x = 0; x < c->w; ++x) {
            float sa = scr[x] * opacity;
            if (sa <= 0.0f) continue;
            float* d = dst + (size_t)x * 4;
            float cr = r, cg = g, cb = b;
            if (useCol) jitter_color(cr, cg, cb, r, g, b, 0u, c->kb1[(size_t)y * c->w + x]);
            float inv = 1.0f - sa;
            d[0] = cr * sa + d[0] * inv;
            d[1] = cg * sa + d[1] * inv;
            d[2] = cb * sa + d[2] * inv;
            d[3] = sa + d[3] * inv;
            scr[x] = 0.0f;
        }
    }
    return 1;
}

// 批渲染：按行带多线程，每带按原顺序重放所有笔触（与顺序执行逐像素一致）
// pts_off: 每股 pts 在 pts 里的起始下标（元素，3 个一组）；params: 每笔 8 个 float
//  params = [size, opacity, r, g, b, hardness, spacing, preset]
EXPORT int paint_strokes_batch(void* p, int layer, const float* pts, const int* pts_off,
                               int n_strokes, const float* params, const int* region_ids,
                               const int* region, const int* row_params,
                               const float* lut_r, const float* lut_a, int lut_n,
                               int nthreads) {
    Ctx* c = (Ctx*)p;
    g_dyn_color = c ? c->color_dyn : 0.0f;
    if (!c || n_strokes <= 0) return 0;
    if (layer < 0 || layer >= (int)c->layers.size()) return 0;
    int W = c->w, H = c->h;
    if (nthreads < 1) nthreads = 1;
    if (nthreads > 16) nthreads = 16;
    const bool useWet = c->wet > 0.0f;
    if (useWet) {
        Layer& LS = c->layers[layer];
        c->snap.resize(LS.px.size());
        std::memcpy(c->snap.data(), LS.px.data(), LS.px.size() * sizeof(float));
    }

    std::vector<int> npts(n_strokes), ends(n_strokes);
    for (int i = 0; i < n_strokes; ++i) {
        int b = pts_off[i];
        int e = (i + 1 < n_strokes) ? pts_off[i + 1] : 0;
        npts[i] = b;
        ends[i] = e;
    }
    // pts_off 只给起点，终点由下一笔起点决定；最后一笔用 row_params[1] 传入总点数
    int total_pts = row_params ? row_params[1] : 0;
    if (n_strokes == 1 || total_pts > 0) ends[n_strokes - 1] = total_pts;

    std::vector<int> sy0v(n_strokes), sy1v(n_strokes);
    for (int i = 0; i < n_strokes; ++i) {
        int b = npts[i], e = ends[i];
        if (e - b < 3) { sy0v[i] = 1 << 30; sy1v[i] = -(1 << 30); continue; }
        float mn = 1e30f, mx = -1e30f;
        for (int k = b; k + 1 < e; k += 3) {
            float y = pts[k + 1];
            if (y < mn) mn = y;
            if (y > mx) mx = y;
        }
        float Rmax = params[(size_t)i * 8 + 0] * 0.5f * 1.15f + 1.0f;
        sy0v[i] = (int)std::floor(mn - Rmax * 1.3f);
        sy1v[i] = (int)std::ceil(mx + Rmax * 1.3f);
    }

    auto work = [&](int ry0, int ry1) {
        ensure_luts();
        std::vector<float> scratch((size_t)(ry1 - ry0) * W, 0.0f);
        std::vector<int> rx0(ry1 - ry0, INT_MAX), rx1(ry1 - ry0, -1);
        std::vector<Stamp> st;
        st.reserve(512);
        std::vector<float> bbuf;
        std::vector<uint32_t> kbuf;        // 每像素获胜 stamp 序号
        const bool useCol = g_dyn_color > 0.0f;
        float tbl[256];
        int last_preset = INT_MIN;
        float last_hard = -1.0f;
        Layer& L = c->layers[layer];
        for (int i = 0; i < n_strokes; ++i) {
            const float* pp = params + (size_t)i * 8;
            float size = pp[0], opacity = pp[1];
            float r = pp[2], g = pp[3], b = pp[4], hardness = pp[5];
            int preset = (int)pp[7];
            if (sy1v[i] < ry0 || sy0v[i] >= ry1) continue;
            int npt = (ends[i] - npts[i]) / 3;
            if (npt < 1) continue;
            if (preset != last_preset || hardness != last_hard) {
                build_tip_table(tbl, hardness, preset);
                last_preset = preset;
                last_hard = hardness;
            }
            const Tip2D* tip2 = resolve_tip2(c, preset);
            make_stamps(st, pts + npts[i], npt, size, lut_r, lut_a, lut_n, (unsigned)i);
            if (st.empty()) continue;
            float maxR = 0.0f;
            for (auto& s : st) if (s.R > maxR) maxR = s.R;
            int rseed = 0;
            if (maxR <= 40.0f) {
                int sx0 = W, sx1 = -1, sy0 = H, sy1 = -1;
                for (auto& s : st) {
                    if (s.R <= 0.0f) continue;
                    int ax0 = (int)std::floor(s.x - s.R * 1.3f), ax1 = (int)std::ceil(s.x + s.R * 1.3f);
                    int ay0 = (int)std::floor(s.y - s.R * 1.3f), ay1 = (int)std::ceil(s.y + s.R * 1.3f);
                    if (ax0 < sx0) sx0 = ax0;
                    if (ax1 > sx1) sx1 = ax1;
                    if (ay0 < sy0) sy0 = ay0;
                    if (ay1 > sy1) sy1 = ay1;
                }
                if (sx1 < sx0 || sy1 < sy0) continue;
                if (sx0 < 0) sx0 = 0;
                if (sx1 > W - 1) sx1 = W - 1;
                if (sy0 < ry0) sy0 = ry0;
                if (sy1 > ry1 - 1) sy1 = ry1 - 1;
                int sw = sx1 - sx0 + 1, sh = sy1 - sy0 + 1;
                if (sw <= 0 || sh <= 0) continue;
                if ((int)bbuf.size() < sw * sh) bbuf.resize((size_t)sw * sh);
                float* bb = bbuf.data();
                std::fill(bb, bb + (size_t)sw * sh, 0.0f);
                if (useCol) kbuf.resize((size_t)sw * sh);
                g_kbuf = useCol ? kbuf.data() : nullptr;
                for (auto& s : st) {
                    if (s.R <= 0.0f) continue;
                    g_kcur = s.k;
                    stamp_bbox(bb, sw, sx0, sy0, sx1, sy1, s.x, s.y, s.R, s.a, preset, s.dx, s.dy,
                               0, tbl, 1.0f / (s.R * s.R), region, W,
                               region ? (region_ids ? region_ids[i] : 0) : 0, tip2);
                }
                for (int y = sy0; y <= sy1; ++y) {
                    float* dst = L.px.data() + (size_t)y * W * 4;
                    float* scr = bb + (size_t)(y - sy0) * sw;
                    for (int x = 0; x < sw; ++x) {
                        float sa = scr[x] * opacity;
                        if (sa <= 0.0f) continue;
                        float* d = dst + (size_t)(sx0 + x) * 4;
                        float cr = r, cg = g, cb = b;
                        if (useCol) jitter_color(cr, cg, cb, r, g, b, (uint32_t)i,
                                                 kbuf[(size_t)(y - sy0) * sw + x]);
                        if (useWet) {
                            const float* sp = c->snap.data() + ((size_t)y * W + (sx0 + x)) * 4;
                            if (sp[3] > 1e-6f) {
                                float ia = 1.0f / sp[3];
                                cr += (sp[0] * ia - cr) * c->wet;
                                cg += (sp[1] * ia - cg) * c->wet;
                                cb += (sp[2] * ia - cb) * c->wet;
                            }
                        }
                        float inv = 1.0f - sa;
                        d[0] = cr * sa + d[0] * inv;
                        d[1] = cg * sa + d[1] * inv;
                        d[2] = cb * sa + d[2] * inv;
                        d[3] = sa + d[3] * inv;
                    }
                }
            } else {
                int tby0 = ry1, tby1 = ry0 - 1;
                if (useCol) kbuf.resize(scratch.size());
                g_kbuf = useCol ? kbuf.data() : nullptr;
                for (auto& s : st) {
                    if (s.R <= 0.0f) continue;
                    g_kcur = s.k;
                    stamp_band(scratch.data(), W, ry0, ry1, s.x, s.y, s.R, s.a, preset, s.dx, s.dy,
                               0, tbl, 1.0f / (s.R * s.R), region,
                               region ? (region_ids ? region_ids[i] : 0) : 0, tby0, tby1, tip2,
                               rx0.data(), rx1.data());
                }
                if (tby1 < tby0) continue;
                for (int y = tby0; y <= tby1; ++y) {
                    int i2 = y - ry0;
                    int xa = rx0[i2], xb = rx1[i2];
                    if (xa > xb) continue;
                    float* dst = L.px.data() + (size_t)y * W * 4;
                    float* scr = scratch.data() + (size_t)i2 * W;
                    for (int x = xa; x <= xb; ++x) {
                        float sa = scr[x] * opacity;
                        if (sa <= 0.0f) continue;
                        float* d = dst + (size_t)x * 4;
                        float cr = r, cg = g, cb = b;
                        if (useCol) jitter_color(cr, cg, cb, r, g, b, (uint32_t)i,
                                                 kbuf[(size_t)i2 * W + x]);
                        if (useWet) {
                            const float* sp = c->snap.data() + ((size_t)y * W + x) * 4;
                            if (sp[3] > 1e-6f) {
                                float ia = 1.0f / sp[3];
                                cr += (sp[0] * ia - cr) * c->wet;
                                cg += (sp[1] * ia - cg) * c->wet;
                                cb += (sp[2] * ia - cb) * c->wet;
                            }
                        }
                        float inv = 1.0f - sa;
                        d[0] = cr * sa + d[0] * inv;
                        d[1] = cg * sa + d[1] * inv;
                        d[2] = cb * sa + d[2] * inv;
                        d[3] = sa + d[3] * inv;
                        scr[x] = 0.0f;
                    }
                    rx0[i2] = INT_MAX;
                    rx1[i2] = -1;
                }
            }
        }
    };

    if (nthreads == 1) {
        work(0, H);
    } else {
        std::vector<std::thread> pool;
        for (int t = 0; t < nthreads; ++t) {
            int r0 = H * t / nthreads;
            int r1 = H * (t + 1) / nthreads;
            pool.emplace_back(work, r0, r1);
        }
        for (auto& th : pool) th.join();
    }
    return 1;
}

EXPORT int paint_composite(void* p, unsigned char* out) {
    Ctx* c = (Ctx*)p;
    if (!c || !out) return 0;
    size_t n = (size_t)c->w * c->h;
    std::vector<float> acc(n * 4, 0.0f);
    for (auto& L : c->layers) {
        const float* s = L.px.data();
        for (size_t i = 0; i < n; ++i) {
            float sa = s[i * 4 + 3];
            if (sa <= 0.0f) continue;
            float* d = &acc[i * 4];
            float da = d[3];
            float inv_sa = 1.0f / sa;
            float sr = s[i * 4 + 0] * inv_sa, sg = s[i * 4 + 1] * inv_sa, sb = s[i * 4 + 2] * inv_sa;
            float dr = da > 0.0f ? d[0] / da : 0.0f;
            float dg = da > 0.0f ? d[1] / da : 0.0f;
            float db = da > 0.0f ? d[2] / da : 0.0f;
            float br = sr, bg = sg, bb = sb;
            blend_px(L.blend, sr, sg, sb, dr, dg, db, br, bg, bb);
            float ao = sa + da * (1.0f - sa);
            if (ao <= 1e-6f) { d[0] = d[1] = d[2] = d[3] = 0.0f; continue; }
            float wa = da * (1.0f - sa);
            d[0] = br * sa + dr * wa;
            d[1] = bg * sa + dg * wa;
            d[2] = bb * sa + db * wa;
            d[3] = ao;
        }
    }
    for (size_t i = 0; i < n; ++i) {
        float a = acc[i * 4 + 3];
        if (a <= 0.0f) { out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = out[i * 4 + 3] = 0; continue; }
        float inv = 1.0f / a;
        auto q = [](float v) { return (unsigned char)std::min(255.0f, std::max(0.0f, v * 255.0f + 0.5f)); };
        out[i * 4 + 0] = q(acc[i * 4 + 0] * inv);
        out[i * 4 + 1] = q(acc[i * 4 + 1] * inv);
        out[i * 4 + 2] = q(acc[i * 4 + 2] * inv);
        out[i * 4 + 3] = q(a);
    }
    return 1;
}

EXPORT int paint_layer_float(void* p, int idx, float* out) {
    Ctx* c = (Ctx*)p;
    if (!c || idx < 0 || idx >= (int)c->layers.size() || !out) return 0;
    memcpy(out, c->layers[idx].px.data(), (size_t)c->w * c->h * 4 * sizeof(float));
    return 1;
}

EXPORT int paint_layer_rgba(void* p, int idx, unsigned char* out) {
    Ctx* c = (Ctx*)p;
    if (!c || idx < 0 || idx >= (int)c->layers.size() || !out) return 0;
    const float* px = c->layers[idx].px.data();
    size_t n = (size_t)c->w * c->h;
    auto q = [](float v) { return (unsigned char)std::min(255.0f, std::max(0.0f, v * 255.0f + 0.5f)); };
    for (size_t i = 0; i < n; ++i) {
        float a = px[i * 4 + 3];
        if (a <= 0.0f) { out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = out[i * 4 + 3] = 0; continue; }
        float inv = 1.0f / a;
        out[i * 4 + 0] = q(px[i * 4 + 0] * inv);
        out[i * 4 + 1] = q(px[i * 4 + 1] * inv);
        out[i * 4 + 2] = q(px[i * 4 + 2] * inv);
        out[i * 4 + 3] = q(a);
    }
    return 1;
}


// ---- 规划辅助：把区域掩膜按方向取样成笔触折线（与 Python hatch 同算法）----
EXPORT int paint_hatch(const unsigned char* mask, int w, int h, float angle,
                       float gap, float minlen, float maxlen,
                       const float* lum, float* out_pts, int* out_off,
                       int max_lines, int max_floats, int* out_total) {
    if (!mask || w <= 0 || h <= 0 || gap <= 0.0f || !out_pts || !out_off) return 0;
    std::vector<int> xs, ys;
    xs.reserve(8192); ys.reserve(8192);
    for (int y = 0; y < h; ++y) {
        const unsigned char* row = mask + (size_t)y * w;
        for (int x = 0; x < w; ++x) if (row[x]) { xs.push_back(x); ys.push_back(y); }
    }
    int n = (int)xs.size();
    if (n < 6) return 0;

    double ca = std::cos((double)angle), sa = std::sin((double)angle);
    std::vector<double> pn((size_t)n), pt((size_t)n);
    double pnmin = 1e300, pnmax = -1e300;
    for (int i = 0; i < n; ++i) {
        pn[i] = -xs[i] * sa + ys[i] * ca;
        pt[i] = xs[i] * ca + ys[i] * sa;
        if (pn[i] < pnmin) pnmin = pn[i];
        if (pn[i] > pnmax) pnmax = pn[i];
    }
    int nb = (int)((pnmax - pnmin) / (double)gap) + 1;
    if (nb < 1) nb = 1;

    std::vector<int> order((size_t)n);
    for (int i = 0; i < n; ++i) order[i] = i;
    std::vector<std::vector<int>> bins((size_t)nb);
    for (int i = 0; i < n; ++i) {
        int b = (int)((pn[i] - pnmin) / (double)gap);
        if (b < 0) b = 0;
        if (b >= nb) b = nb - 1;
        bins[(size_t)b].push_back(i);
    }

    int lines = 0, nfl = 0;
    const double brk_gap = (double)gap * 1.8;
    for (int b = 0; b < nb; ++b) {
        std::vector<int>& idx = bins[(size_t)b];
        if (idx.size() < 3) continue;
        std::sort(idx.begin(), idx.end(), [&](int a, int c) { return pt[a] < pt[c]; });
        size_t ns = idx.size();
        size_t start = 0;
        while (start < ns) {
            size_t end = start;
            while (end + 1 < ns && (pt[idx[end + 1]] - pt[idx[end]]) <= brk_gap) ++end;
            double L = pt[idx[end]] - pt[idx[start]];
            size_t segn = end - start + 1;
            if (L >= (double)minlen && segn >= 3) {
                int nseg = (int)std::ceil(L / (double)maxlen);
                if (nseg < 1) nseg = 1;
                int step = (int)(segn / (size_t)nseg);
                if (step < 1) step = 1;
                for (size_t c0 = 0; c0 + 1 < segn; c0 += (size_t)step) {
                    size_t take = std::min((size_t)step + 1, segn - c0);
                    if (take < 2) continue;
                    if (lines >= max_lines) { if (out_total) *out_total = nfl; return lines; }
                    if (nfl + (int)take * 3 > max_floats) { if (out_total) *out_total = nfl; return lines; }
                    out_off[lines] = nfl;
                    for (size_t k = 0; k < take; ++k) {
                        int i2 = idx[c0 + k];
                        int px = xs[i2], py = ys[i2];
                        float pr = 1.05f - lum[(size_t)py * w + px] / 255.0f * 0.35f;
                        if (pr < 0.6f) pr = 0.6f;
                        if (pr > 1.0f) pr = 1.0f;
                        out_pts[nfl++] = (float)px;
                        out_pts[nfl++] = (float)py;
                        out_pts[nfl++] = pr;
                    }
                    ++lines;
                }
            }
            start = end + 1;
        }
    }
    if (out_total) *out_total = nfl;
    return lines;
}

// ---- 位图笔尖（增量能力；公式预设 0..9 不受影响）----
EXPORT int paint_tip_add(void* p, const float* a, int tw, int th) {
    Ctx* c = (Ctx*)p;
    if (!c || !a || tw < 2 || th < 2) return -1;
    Tip2D t;
    t.w = tw; t.h = th;
    t.a.assign(a, a + (size_t)tw * th);
    c->tips.push_back(std::move(t));
    return (int)c->tips.size() - 1;
}

EXPORT int paint_tip_count(void* p) {
    Ctx* c = (Ctx*)p;
    return c ? (int)c->tips.size() : 0;
}

EXPORT int paint_layer_blend(void* p, int idx) {
    Ctx* c = (Ctx*)p;
    if (!c || idx < 0 || idx >= (int)c->layers.size()) return -1;
    return c->layers[idx].blend;
}

