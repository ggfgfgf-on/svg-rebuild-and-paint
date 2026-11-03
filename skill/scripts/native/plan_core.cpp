// plan_core.cpp —— 区域规划内核（第 1 步）：像素表 → 分位带 → hatch → 笔触数组
// 只负责几何与颜色；随机数、preset 名称映射仍留在 Python。
#include <cmath>
#include <cstring>
#include <cstdint>
#include <vector>
#include <algorithm>
#include <limits>

#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C"
#endif

namespace {

struct RParams {
    int band_count;
    float bed_min, bed_max, bed_thick_mul, bed_area_div, bed_opacity;
    float bed_gap_frac, bed_minlen, bed_maxlen, bed_thick;
    float band_size_div, band_clamp_lo, band_clamp_hi, band_size_scale, band_thick_frac;
    float band_gap_frac, band_stroke_scale, band_minlen, band_maxlen, band_opacity;
    float angle;
    float thick;   // 区域厚度
    int band_min_px;   // 明度带最少像素数（与 Python 的 min_px 对齐）
    int sh_bands;      // 阴影只做前几条带
    float sh_off_frac, sh_gap_mul, sh_stroke_scale, sh_opacity, sh_color_mul;
    float sh_minlen, sh_maxlen, lx, ly;
};

struct Out {
    float* vals; int maxf; int nf;
    int* meta; int maxs; int ns;
    float* metaf;   // 每笔: [layer(0=bed,1=band), band_idx, npts, size*1000, opacity*1000, r,g,b]
};

// 折线取样：像素列表中按方向投影分箱 → 排序 → 断段 → 按 maxlen 切块
static void hatch_px(const int* px, const int* py, int n, const float* lum, int W,
                     float angle, float gap, float minlen, float maxlen,
                     std::vector<std::vector<int> >& lines) {
    if (n < 6 || gap <= 0.0f) return;
    double ca = std::cos((double)angle), sa = std::sin((double)angle);
    int ox = px[0], oy = py[0];
    for (int i = 1; i < n; ++i) { if (px[i] < ox) ox = px[i]; if (py[i] < oy) oy = py[i]; }
    std::vector<double> pn((size_t)n), pt((size_t)n);
    double mn = 1e300, mx = -1e300;
    for (int i = 0; i < n; ++i) {
        double lx = (double)(px[i] - ox), ly = (double)(py[i] - oy);
        pn[i] = -lx * sa + ly * ca;
        pt[i] = lx * ca + ly * sa;
        if (pn[i] < mn) mn = pn[i];
        if (pn[i] > mx) mx = pn[i];
    }
    int nb = (int)((mx - mn) / (double)gap) + 1;
    if (nb < 1) nb = 1;
    std::vector<std::vector<int> > bins((size_t)nb);
    for (int i = 0; i < n; ++i) {
        int b = (int)((pn[i] - mn) / (double)gap);
        if (b < 0) b = 0;
        if (b >= nb) b = nb - 1;
        bins[(size_t)b].push_back(i);
    }
    const double brk = (double)gap * 1.8;
    for (int b = 0; b < nb; ++b) {
        std::vector<int>& idx = bins[(size_t)b];
        if (idx.size() < 3) continue;
        std::sort(idx.begin(), idx.end(), [&](int a, int c) { return pt[a] < pt[c]; });
        size_t i = 0, m = idx.size();
        while (i < m) {
            size_t j = i;
            while (j + 1 < m && (pt[idx[j + 1]] - pt[idx[j]]) <= brk) ++j;
            double L = pt[idx[j]] - pt[idx[i]];
            size_t segn = j - i + 1;
            if (L >= (double)minlen && segn >= 3) {
                int nseg = (int)std::ceil(L / (double)maxlen);
                if (nseg < 1) nseg = 1;
                int step = (int)(segn / (size_t)nseg);
                if (step < 1) step = 1;
                for (size_t c0 = 0; c0 + 1 < segn; c0 += (size_t)step) {
                    size_t take = std::min((size_t)step + 1, segn - c0);
                    if (take < 2) continue;
                    std::vector<int> line;
                    line.reserve(take * 3);
                    for (size_t k = 0; k < take; ++k) {
                        int i2 = idx[c0 + k];
                        float pr = 1.05f - lum[(size_t)py[i2] * W + px[i2]] / 255.0f * 0.35f;
                        if (pr < 0.6f) pr = 0.6f;
                        if (pr > 1.0f) pr = 1.0f;
                        line.push_back(px[i2]);
                        line.push_back(py[i2]);
                        line.push_back((int)(pr * 10000.0f));
                    }
                    lines.push_back(line);
                }
            }
            i = j + 1;
        }
    }
}

static void push_stroke_off(Out& o, int layer, int band, float size, float opacity,
                            float r, float g, float b, const std::vector<int>& line,
                            float dx, float dy) {
    if (o.ns >= o.maxs) return;
    int npts = (int)line.size() / 3;
    if (npts < 2) return;
    if (o.nf + (int)line.size() > o.maxf) return;
    o.meta[o.ns * 8 + 0] = layer;
    o.meta[o.ns * 8 + 1] = band;
    o.meta[o.ns * 8 + 2] = npts;
    o.meta[o.ns * 8 + 3] = 0;
    o.meta[o.ns * 8 + 4] = 0;
    o.meta[o.ns * 8 + 5] = 0;
    o.meta[o.ns * 8 + 6] = 0;
    o.meta[o.ns * 8 + 7] = 0;
    if (o.metaf) {
        o.metaf[o.ns * 5 + 0] = size;
        o.metaf[o.ns * 5 + 1] = opacity;
        o.metaf[o.ns * 5 + 2] = r;
        o.metaf[o.ns * 5 + 3] = g;
        o.metaf[o.ns * 5 + 4] = b;
    }
    for (size_t k = 0; k < line.size(); k += 3) {
        o.vals[o.nf++] = (float)line[k] + dx;
        o.vals[o.nf++] = (float)line[k + 1] + dy;
        o.vals[o.nf++] = (float)line[k + 2] / 10000.0f;
    }
    ++o.ns;
}

static void push_stroke(Out& o, int layer, int band, float size, float opacity,
                        float r, float g, float b, const std::vector<int>& line) {
    if (o.ns >= o.maxs) return;
    int npts = (int)line.size() / 3;
    if (npts < 2) return;
    if (o.nf + (int)line.size() > o.maxf) return;
    o.meta[o.ns * 8 + 0] = layer;
    o.meta[o.ns * 8 + 1] = band;
    o.meta[o.ns * 8 + 2] = npts;
    o.meta[o.ns * 8 + 3] = 0;
    o.meta[o.ns * 8 + 4] = 0;
    o.meta[o.ns * 8 + 5] = 0;
    o.meta[o.ns * 8 + 6] = 0;
    o.meta[o.ns * 8 + 7] = 0;
    if (o.metaf) {
        o.metaf[o.ns * 5 + 0] = size;
        o.metaf[o.ns * 5 + 1] = opacity;
        o.metaf[o.ns * 5 + 2] = r;
        o.metaf[o.ns * 5 + 3] = g;
        o.metaf[o.ns * 5 + 4] = b;
    }
    for (size_t k = 0; k < line.size(); k += 3) {
        o.vals[o.nf++] = (float)line[k];
        o.vals[o.nf++] = (float)line[k + 1];
        o.vals[o.nf++] = (float)line[k + 2] / 10000.0f;
    }
    ++o.ns;
}

static float main_dir_px(const int* px, const int* py, int n,
                             const float* gx, const float* gy, const float* gmag, int W) {
    if (n < 12) return 0.0f;
    double ss = 0.0, sc = 0.0;
    for (int i = 0; i < n; ++i) {
        size_t p = (size_t)py[i] * W + px[i];
        double a = std::atan2((double)gy[p], (double)gx[p]) + 1.5707963267948966;
        double w = (double)gmag[p] + 0.05;
        ss += std::sin(2.0 * a) * w;
        sc += std::cos(2.0 * a) * w;
    }
    return (float)(0.5 * std::atan2(ss, sc));
}

static void median_rgb(const unsigned char* ref, int W, const int* px, const int* py,
                       const int* ids, int n, float* out) {
    if (n <= 0) { out[0] = out[1] = out[2] = 0; return; }
    std::vector<int> c[3];
    for (int k = 0; k < 3; ++k) c[k].resize((size_t)n);
    for (int i = 0; i < n; ++i) {
        int p = ((size_t)py[ids[i]] * W + px[ids[i]]) * 3;
        for (int k = 0; k < 3; ++k) c[k][(size_t)i] = ref[p + k];
    }
    for (int k = 0; k < 3; ++k) {
        size_t mid = (size_t)(n / 2);
        std::nth_element(c[k].begin(), c[k].begin() + mid, c[k].end());
        double hi = (double)c[k][mid];
        if (n % 2 == 0) {
            std::nth_element(c[k].begin(), c[k].begin() + (mid - 1), c[k].end());
            double lo = (double)c[k][mid - 1];
            out[k] = (float)((lo + hi) * 0.5);
        } else {
            out[k] = (float)hi;
        }
    }
}

}  // namespace

EXPORT int plan_region_emit(const int* xs, const int* ys, int n,
                            const float* lum, const unsigned char* ref, int W, int H,
                            const float* gx, const float* gy, const float* gmag,
                            const RParams* rp,
                            float* out_vals, int max_floats,
                            int* out_meta, float* out_metaf, int max_strokes,
                            int* out_nstrokes, int* out_nfloats) {
    if (!xs || !ys || n <= 0 || !rp || !out_vals || !out_meta) return 0;
    struct SavedBand {
        std::vector<int> bx, by; int cnt; double bsize; float ba0; float col[3];
    };
    std::vector<SavedBand> saved;
    Out o; o.vals = out_vals; o.maxf = max_floats; o.nf = 0;
    o.meta = out_meta; o.maxs = max_strokes; o.ns = 0;
    o.metaf = out_metaf;
    std::vector<int> ids((size_t)n);
    for (int i = 0; i < n; ++i) ids[(size_t)i] = i;

    // 色床：整区域一个方向、一个尺寸
    float base[3];
    median_rgb(ref, W, xs, ys, ids.data(), n, base);
    std::vector<std::vector<int> > bed_lines;
    hatch_px(xs, ys, n, lum, W, rp->angle, rp->bed_thick * rp->bed_gap_frac,
             rp->bed_minlen, rp->bed_maxlen, bed_lines);
    for (size_t i = 0; i < bed_lines.size(); ++i) {
        int band = (int)(i % (size_t)std::max(1, rp->band_count));
        push_stroke(o, 0, band, rp->bed_thick, rp->bed_opacity, base[0], base[1], base[2], bed_lines[i]);
    }

    // 分位带：复刻 numpy.percentile 线性插值（含离群点时带间会重叠）
    int nb = rp->band_count > 0 ? rp->band_count : 1;
    std::vector<double> lv((size_t)n);
    for (int i = 0; i < n; ++i) lv[(size_t)i] = (double)lum[(size_t)ys[i] * W + xs[i]];
    std::vector<double> sv(lv);
    std::sort(sv.begin(), sv.end());
    std::vector<double> qs((size_t)nb + 1);
    for (int k = 0; k <= nb; ++k) {
        double pos = (double)(n - 1) * (double)k / (double)nb;
        int i0 = (int)std::floor(pos), i1 = (int)std::ceil(pos);
        if (i1 >= n) i1 = n - 1;
        if (i0 < 0) i0 = 0;
        double fr = pos - (double)i0;
        qs[(size_t)k] = sv[(size_t)i0] * (1.0 - fr) + sv[(size_t)i1] * fr;
    }

    for (int b = 0; b < nb; ++b) {
        std::vector<int> sel;
        sel.reserve((size_t)n);
        for (int i = 0; i < n; ++i) {
            double v = lv[(size_t)i];
            if (v >= qs[(size_t)b] && v <= qs[(size_t)(b + 1)]) sel.push_back(i);
        }
        int cnt = (int)sel.size();
        if (cnt < rp->band_min_px) continue;
        std::vector<int> bx((size_t)cnt), by((size_t)cnt);
        for (int k = 0; k < cnt; ++k) { bx[(size_t)k] = xs[sel[(size_t)k]]; by[(size_t)k] = ys[sel[(size_t)k]]; }
        float bcol[3];
        median_rgb(ref, W, xs, ys, sel.data(), cnt, bcol);
        double bsize = std::sqrt((double)cnt) / (double)rp->band_size_div;
        if (bsize < (double)rp->band_clamp_lo) bsize = (double)rp->band_clamp_lo;
        if (bsize > (double)rp->band_clamp_hi) bsize = (double)rp->band_clamp_hi;
        bsize *= (double)rp->band_size_scale;
        double cap = std::max(6.0, (double)rp->thick * (double)rp->band_thick_frac);
        if (bsize > cap) bsize = cap;
        double bstroke = bsize * (double)rp->band_stroke_scale;
        float ba0 = main_dir_px(bx.data(), by.data(), cnt, gx, gy, gmag, W);
        std::vector<std::vector<int> > lines;
        hatch_px(bx.data(), by.data(), cnt, lum, W, ba0, (float)(bsize * (double)rp->band_gap_frac),
                 rp->band_minlen, rp->band_maxlen, lines);
        for (size_t i = 0; i < lines.size(); ++i) {
            push_stroke(o, 1, b, (float)bstroke, rp->band_opacity, bcol[0], bcol[1], bcol[2], lines[i]);
        }
        if (b < rp->sh_bands) {
            SavedBand sv;
            sv.bx = bx; sv.by = by; sv.cnt = cnt; sv.bsize = bsize; sv.ba0 = ba0;
            sv.col[0] = bcol[0]; sv.col[1] = bcol[1]; sv.col[2] = bcol[2];
            saved.push_back(sv);
        }
    }
    for (size_t k = 0; k < saved.size(); ++k) {
        const SavedBand& sv = saved[k];
        double off = (double)rp->sh_off_frac * sv.bsize;
        float dx = (float)(-(double)rp->lx * off), dy = (float)(-(double)rp->ly * off);
        std::vector<std::vector<int> > shl;
        hatch_px(sv.bx.data(), sv.by.data(), sv.cnt, lum, W, sv.ba0,
                 (float)(sv.bsize * (double)rp->sh_gap_mul),
                 rp->sh_minlen, rp->sh_maxlen, shl);
        for (size_t i = 0; i < shl.size(); ++i) {
            push_stroke_off(o, 2, (int)k, (float)(sv.bsize * (double)rp->sh_stroke_scale),
                            rp->sh_opacity,
                            (float)((double)sv.col[0] * (double)rp->sh_color_mul),
                            (float)((double)sv.col[1] * (double)rp->sh_color_mul),
                            (float)((double)sv.col[2] * (double)rp->sh_color_mul), shl[i], dx, dy);
        }
    }
    if (out_nstrokes) *out_nstrokes = o.ns;
    if (out_nfloats) *out_nfloats = o.nf;
    return o.ns;
}

EXPORT int plan_hatch_test(const int* px, const int* py, int n,
                           const float* lum, int W,
                           float angle, float gap, float minlen, float maxlen) {
    std::vector<std::vector<int> > lines;
    hatch_px(px, py, n, lum, W, angle, gap, minlen, maxlen, lines);
    return (int)lines.size();
}
