// paint_fx.cpp - GPU compute 版 smudge / impasto（float32 全程，不做 8bit 量化）
// 语义与 scripts/brush_ext.py 的 smudge_rgba / impasto_rgba 一一对应。
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "d3dcompiler.lib")

#define EXPORT extern "C" __declspec(dllexport)

static const char* kHLSL = R"HLSL(
struct FX {
    int W, H;
    int bx0, by0;
    int bw, bh;
    int combine, pad0;
    float cx, cy, dirx, diry;
    float radius, amount, hardness, shininess;
    float lx, ly, lz, ambient;
    float hx, hy, hz, spec;
};
cbuffer C : register(b0) { FX q; };
StructuredBuffer<float4> src : register(t0);
RWStructuredBuffer<float4> dst : register(u0);

float4 sample_bilinear(float x, float y) {
    float wx = (float)q.W - 1.001f;
    float wy = (float)q.H - 1.001f;
    x = clamp(x, 0.0f, wx);
    y = clamp(y, 0.0f, wy);
    int x0 = (int)floor(x);
    int y0 = (int)floor(y);
    float fx = x - (float)x0;
    float fy = y - (float)y0;
    int x1 = min(x0 + 1, q.W - 1);
    int y1 = min(y0 + 1, q.H - 1);
    float4 a = src[(uint)y0 * q.W + x0];
    float4 b = src[(uint)y0 * q.W + x1];
    float4 c = src[(uint)y1 * q.W + x0];
    float4 d = src[(uint)y1 * q.W + x1];
    return a * (1.0f - fx) * (1.0f - fy) + b * fx * (1.0f - fy)
         + c * (1.0f - fx) * fy + d * fx * fy;
}

[numthreads(8, 8, 1)]
void cs_smudge(uint3 dt : SV_DispatchThreadID) {
    if ((int)dt.x >= q.bw || (int)dt.y >= q.bh) return;
    int x = q.bx0 + (int)dt.x;
    int y = q.by0 + (int)dt.y;
    uint idx = (uint)y * (uint)q.W + (uint)x;
    float dx = (float)x - q.cx;
    float dy = (float)y - q.cy;
    float d = sqrt(dx * dx + dy * dy) / max(q.radius, 1e-3f);
    float fall = clamp((1.0f - d) / max(1e-3f, 1.0f - q.hardness), 0.0f, 1.0f);
    fall = fall * fall * (3.0f - 2.0f * fall) * q.amount;
    float4 s = sample_bilinear((float)x - q.dirx * q.radius * 0.6f,
                               (float)y - q.diry * q.radius * 0.6f);
    float4 o = dst[idx];
    dst[idx] = o * (1.0f - fall) + s * fall;
}

float height_at(int x, int y) {
    x = clamp(x, 0, q.W - 1);
    y = clamp(y, 0, q.H - 1);
    return src[(uint)y * (uint)q.W + (uint)x].a;
}

[numthreads(8, 8, 1)]
void cs_impasto(uint3 dt : SV_DispatchThreadID) {
    if ((int)dt.x >= q.W || (int)dt.y >= q.H) return;
    int x = (int)dt.x, y = (int)dt.y;
    uint idx = (uint)y * (uint)q.W + (uint)x;
    float4 p = src[idx];
    float gx, gy;
    if (q.W == 1) gx = 0.0f;
    else if (x == 0) gx = height_at(1, y) - height_at(0, y);
    else if (x == q.W - 1) gx = height_at(x, y) - height_at(x - 1, y);
    else gx = (height_at(x + 1, y) - height_at(x - 1, y)) * 0.5f;
    if (q.H == 1) gy = 0.0f;
    else if (y == 0) gy = height_at(x, 1) - height_at(x, 0);
    else if (y == q.H - 1) gy = height_at(x, y) - height_at(x, y - 1);
    else gy = (height_at(x, y + 1) - height_at(x, y - 1)) * 0.5f;

    float nx = -gx, ny = -gy, nz = 1.0f;
    float ln = sqrt(nx * nx + ny * ny + nz * nz) + 1e-9f;
    nx /= ln; ny /= ln; nz /= ln;
    float lam = clamp(nx * q.lx + ny * q.ly + nz * q.lz, 0.0f, 1.0f);
    float nh = clamp(nx * q.hx + ny * q.hy + nz * q.hz, 0.0f, 1.0f);
    float sp;
    if (q.shininess == 24.0f) {
        float p2 = nh * nh, p4 = p2 * p2, p8 = p4 * p4, p16 = p8 * p8;
        sp = p16 * p8 * q.spec;          // nh^24 连乘，与 CPU 的 np.power 等价到 float32
    } else {
        sp = pow(nh, q.shininess) * q.spec;
    }
    float shade = q.ambient + (1.0f - q.ambient) * lam;
    float3 lit = p.rgb * shade + sp;
    float3 out_rgb = (q.combine == 0) ? saturate(p.rgb * 0.35f + lit * 0.65f) : saturate(lit);
    dst[idx] = float4(out_rgb, p.a);
}
)HLSL";

struct FX {
    int W = 0, H = 0;
    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    ID3D11ComputeShader* cs_smudge = nullptr;
    ID3D11ComputeShader* cs_impasto = nullptr;
    ID3D11Buffer* data = nullptr;
    ID3D11ShaderResourceView* dataSrv = nullptr;
    ID3D11UnorderedAccessView* dataUav = nullptr;
    ID3D11Buffer* snap = nullptr;
    ID3D11ShaderResourceView* snapSrv = nullptr;
    ID3D11Buffer* stage = nullptr;
    ID3D11Buffer* cb = nullptr;
};

struct FXCb {
    int W, H;
    int bx0, by0;
    int bw, bh;
    int combine, pad0;
    float cx, cy, dirx, diry;
    float radius, amount, hardness, shininess;
    float lx, ly, lz, ambient;
    float hx, hy, hz, spec;
};

static ID3D11ComputeShader* compile_cs(ID3D11Device* dev, const char* src, const char* entry) {
    ID3DBlob* bc = nullptr; ID3DBlob* err = nullptr;
    HRESULT hr = D3DCompile(src, strlen(src), nullptr, nullptr, nullptr, entry, "cs_5_0", 0, 0, &bc, &err);
    if (FAILED(hr)) {
        printf("[fx] compile %s failed: %s\n", entry, err ? (char*)err->GetBufferPointer() : "?");
        if (bc) bc->Release();
        return nullptr;
    }
    ID3D11ComputeShader* sh = nullptr;
    dev->CreateComputeShader(bc->GetBufferPointer(), bc->GetBufferSize(), nullptr, &sh);
    bc->Release();
    return sh;
}

static ID3D11Buffer* make_sb(ID3D11Device* dev, UINT bytes, UINT bind, UINT cpu, UINT misc) {
    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = bytes;
    bd.Usage = cpu ? D3D11_USAGE_STAGING : D3D11_USAGE_DEFAULT;
    bd.BindFlags = cpu ? 0 : bind;
    bd.CPUAccessFlags = cpu;
    bd.MiscFlags = misc;
    bd.StructureByteStride = 16;
    ID3D11Buffer* b = nullptr;
    dev->CreateBuffer(&bd, nullptr, &b);
    return b;
}

static void bind_all(FX* f) {
    ID3D11ShaderResourceView* srv = f->snapSrv;
    ID3D11UnorderedAccessView* uav = f->dataUav;
    ID3D11Buffer* cbuf = f->cb;
    f->ctx->CSSetShaderResources(0, 1, &srv);
    f->ctx->CSSetUnorderedAccessViews(0, 1, &uav, nullptr);
    f->ctx->CSSetConstantBuffers(0, 1, &cbuf);
}

EXPORT void* fx_create(int w, int h) {
    if (w <= 0 || h <= 0) return nullptr;
    FX* f = new FX();
    f->W = w; f->H = h;
    D3D_FEATURE_LEVEL lv = D3D_FEATURE_LEVEL_11_0;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, &lv, 1,
                                   D3D11_SDK_VERSION, &f->dev, nullptr, &f->ctx);
    if (FAILED(hr)) {
        hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, &lv, 1,
                               D3D11_SDK_VERSION, &f->dev, nullptr, &f->ctx);
    }
    if (FAILED(hr)) { delete f; return nullptr; }
    f->cs_smudge = compile_cs(f->dev, kHLSL, "cs_smudge");
    f->cs_impasto = compile_cs(f->dev, kHLSL, "cs_impasto");
    if (!f->cs_smudge || !f->cs_impasto) { return nullptr; }
    UINT bytes = (UINT)((size_t)w * h * 16);
    f->data = make_sb(f->dev, bytes, D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS, 0, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED);
    f->snap = make_sb(f->dev, bytes, D3D11_BIND_SHADER_RESOURCE, 0, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED);
    f->stage = make_sb(f->dev, bytes, 0, D3D11_CPU_ACCESS_READ | D3D11_CPU_ACCESS_WRITE, D3D11_RESOURCE_MISC_BUFFER_STRUCTURED);
    f->dev->CreateShaderResourceView(f->data, nullptr, &f->dataSrv);
    f->dev->CreateUnorderedAccessView(f->data, nullptr, &f->dataUav);
    f->dev->CreateShaderResourceView(f->snap, nullptr, &f->snapSrv);
    D3D11_BUFFER_DESC cbd{};
    cbd.ByteWidth = sizeof(FXCb);
    cbd.Usage = D3D11_USAGE_DEFAULT;
    cbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    f->dev->CreateBuffer(&cbd, nullptr, &f->cb);
    return f;
}

EXPORT void fx_destroy(void* p) {
    FX* f = (FX*)p;
    if (!f) return;
    if (f->cs_smudge) f->cs_smudge->Release();
    if (f->cs_impasto) f->cs_impasto->Release();
    if (f->dataUav) f->dataUav->Release();
    if (f->dataSrv) f->dataSrv->Release();
    if (f->snapSrv) f->snapSrv->Release();
    if (f->data) f->data->Release();
    if (f->snap) f->snap->Release();
    if (f->stage) f->stage->Release();
    if (f->cb) f->cb->Release();
    if (f->ctx) f->ctx->Release();
    if (f->dev) f->dev->Release();
    delete f;
}

EXPORT int fx_upload(void* p, const float* rgba) {
    FX* f = (FX*)p;
    if (!f || !rgba) return 0;
    UINT bytes = (UINT)((size_t)f->W * f->H * 16);
    f->ctx->UpdateSubresource(f->data, 0, nullptr, rgba, 0, 0);
    f->ctx->CopyResource(f->snap, f->data);
    (void)bytes;
    return 1;
}

EXPORT int fx_download(void* p, float* rgba) {
    FX* f = (FX*)p;
    if (!f || !rgba) return 0;
    f->ctx->CopyResource(f->stage, f->data);
    D3D11_MAPPED_SUBRESOURCE m{};
    if (FAILED(f->ctx->Map(f->stage, 0, D3D11_MAP_READ, 0, &m))) return 0;
    memcpy(rgba, m.pData, (size_t)f->W * f->H * 16);
    f->ctx->Unmap(f->stage, 0);
    return 1;
}

EXPORT int fx_smudge(void* p, const float* path, int npts, float radius, float amount,
                     float hardness, float step) {
    FX* f = (FX*)p;
    if (!f || !path || npts < 2 || amount <= 0.0f) return 0;
    if (step <= 0.0f) step = radius * 0.25f > 1.0f ? radius * 0.25f : 1.0f;
    f->ctx->CopyResource(f->snap, f->data);
    bind_all(f);
    f->ctx->CSSetShader(f->cs_smudge, nullptr, 0);
    int r = (int)ceilf(radius * 1.3f);
    for (int i = 1; i < npts; ++i) {
        float x0 = path[(i - 1) * 3 + 0], y0 = path[(i - 1) * 3 + 1];
        float x1 = path[i * 3 + 0], y1 = path[i * 3 + 1];
        float sx = x1 - x0, sy = y1 - y0;
        float ln = sqrtf(sx * sx + sy * sy);
        if (ln < 1e-3f) continue;
        float dxr = sx / ln, dyr = sy / ln;
        int n = (int)(ln / step);
        if (n < 1) n = 1;
        for (int k = 0; k <= n; ++k) {
            float t = (float)k / (float)n;
            float cx = x0 + sx * t, cy = y0 + sy * t;
            int bx0 = (int)cx - r; if (bx0 < 0) bx0 = 0;
            int bx1 = (int)cx + r + 1; if (bx1 > f->W) bx1 = f->W;
            int by0 = (int)cy - r; if (by0 < 0) by0 = 0;
            int by1 = (int)cy + r + 1; if (by1 > f->H) by1 = f->H;
            int bw = bx1 - bx0, bh = by1 - by0;
            if (bw <= 0 || bh <= 0) continue;
            FXCb cb{};
            cb.W = f->W; cb.H = f->H;
            cb.bx0 = bx0; cb.by0 = by0; cb.bw = bw; cb.bh = bh;
            cb.cx = cx; cb.cy = cy; cb.dirx = dxr; cb.diry = dyr;
            cb.radius = radius; cb.amount = amount; cb.hardness = hardness;
            f->ctx->UpdateSubresource(f->cb, 0, nullptr, &cb, 0, 0);
            f->ctx->Dispatch((UINT)((bw + 7) / 8), (UINT)((bh + 7) / 8), 1);
        }
    }
    return 1;
}

EXPORT int fx_impasto(void* p, float lx, float ly, float lz,
                      float ambient, float spec, float shininess, int combine) {
    FX* f = (FX*)p;
    if (!f) return 0;
    float ll = sqrtf(lx * lx + ly * ly + lz * lz) + 1e-9f;
    float Lx = lx / ll, Ly = ly / ll, Lz = lz / ll;
    float hx = Lx, hy = Ly, hz = Lz + 1.0f;
    float hl = sqrtf(hx * hx + hy * hy + hz * hz) + 1e-9f;
    hx /= hl; hy /= hl; hz /= hl;

    f->ctx->CopyResource(f->snap, f->data);
    bind_all(f);
    f->ctx->CSSetShader(f->cs_impasto, nullptr, 0);
    FXCb cb{};
    cb.W = f->W; cb.H = f->H;
    cb.bx0 = 0; cb.by0 = 0; cb.bw = f->W; cb.bh = f->H;
    cb.lx = Lx; cb.ly = Ly; cb.lz = Lz;
    cb.hx = hx; cb.hy = hy; cb.hz = hz;
    cb.ambient = ambient; cb.spec = spec; cb.combine = combine; cb.shininess = shininess;
    f->ctx->UpdateSubresource(f->cb, 0, nullptr, &cb, 0, 0);
    f->ctx->Dispatch((UINT)((f->W + 7) / 8), (UINT)((f->H + 7) / 8), 1);
    return 1;
}
