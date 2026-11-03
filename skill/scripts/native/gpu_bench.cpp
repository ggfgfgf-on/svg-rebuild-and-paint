// D3D11 stamp throughput bench: max-splat N stamps into a W*H canvas
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi.h>
#include <cstdio>
#include <cstring>
#include <vector>
#include <chrono>

static const char* kSrc = R"HLSL(
StructuredBuffer<float4> stamps : register(t0);
RWStructuredBuffer<uint> canvas : register(u0);
cbuffer P : register(b0) { uint N; uint W; uint H; uint nthreads; };

[numthreads(256,1,1)]
void main(uint3 dt : SV_DispatchThreadID) {
    uint T = nthreads;
    for (uint i = dt.x; i < N; i += T) {
        float4 s = stamps[i];
        int R = (int)(s.z + 0.5f);
        float inv = 1.0f / (s.z * s.z);
        int x0 = max(0, (int)s.x - R), x1 = min((int)W - 1, (int)s.x + R);
        int y0 = max(0, (int)s.y - R), y1 = min((int)H - 1, (int)s.y + R);
        for (int y = y0; y <= y1; ++y) {
            for (int x = x0; x <= x1; ++x) {
                float dx = (float)x + 0.5f - s.x, dy = (float)y + 0.5f - s.y;
                float e2 = (dx*dx + dy*dy) * inv;
                if (e2 > 1.0f) continue;
                float a = s.w * (1.0f - e2);
                uint idx = (uint)y * W + (uint)x;
                InterlockedMax(canvas[idx], asuint(a));
            }
        }
    }
}
)HLSL";

int main() {
    ID3D11Device* dev = nullptr; ID3D11DeviceContext* ctx = nullptr;
    D3D_FEATURE_LEVEL fl;
    if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                 D3D11_SDK_VERSION, &dev, &fl, &ctx))) { printf("device fail\n"); return 1; }
    const uint32_t W = 900, H = 1100;
    const int NS = 3000000;
    std::vector<float> st(NS * 4);
    srand(1234);
    for (int i = 0; i < NS; ++i) {
        st[i * 4 + 0] = (float)(rand() % W);
        st[i * 4 + 1] = (float)(rand() % H);
        st[i * 4 + 2] = 20.0f;
        st[i * 4 + 3] = 0.9f;
    }
    D3D11_BUFFER_DESC bd{}; bd.Usage = D3D11_USAGE_DEFAULT;
    bd.BindFlags = D3D11_BIND_SHADER_RESOURCE; bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = 16; bd.ByteWidth = NS * 16;
    D3D11_SUBRESOURCE_DATA sd{}; sd.pSysMem = st.data();
    ID3D11Buffer* sb = nullptr; dev->CreateBuffer(&bd, &sd, &sb);
    D3D11_SHADER_RESOURCE_VIEW_DESC srvd{}; srvd.Format = DXGI_FORMAT_UNKNOWN;
    srvd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; srvd.Buffer.NumElements = NS;
    ID3D11ShaderResourceView* srv = nullptr; dev->CreateShaderResourceView(sb, &srvd, &srv);

    const UINT NPIX = W * H;
    std::vector<uint32_t> zeros(NPIX, 0);
    D3D11_BUFFER_DESC cb{}; cb.Usage = D3D11_USAGE_DEFAULT;
    cb.BindFlags = D3D11_BIND_UNORDERED_ACCESS; cb.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    cb.StructureByteStride = 4; cb.ByteWidth = NPIX * 4;
    D3D11_SUBRESOURCE_DATA cd{}; cd.pSysMem = zeros.data();
    ID3D11Buffer* canvas = nullptr; dev->CreateBuffer(&cb, &cd, &canvas);
    D3D11_UNORDERED_ACCESS_VIEW_DESC uavd{}; uavd.Format = DXGI_FORMAT_UNKNOWN;
    uavd.ViewDimension = D3D11_UAV_DIMENSION_BUFFER; uavd.Buffer.NumElements = NPIX;
    ID3D11UnorderedAccessView* uav = nullptr; dev->CreateUnorderedAccessView(canvas, &uavd, &uav);

    D3D11_BUFFER_DESC pbd{}; pbd.Usage = D3D11_USAGE_DEFAULT; pbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    pbd.ByteWidth = 16;
    struct P { uint32_t N, W, H, T; } par{ (uint32_t)NS, W, H, 256u * 1024u };
    D3D11_SUBRESOURCE_DATA pd{}; pd.pSysMem = &par;
    ID3D11Buffer* pbuf = nullptr; dev->CreateBuffer(&pbd, &pd, &pbuf);

    ID3DBlob* cs = nullptr; ID3DBlob* err = nullptr;
    if (FAILED(D3DCompile(kSrc, strlen(kSrc), nullptr, nullptr, nullptr, "main", "cs_5_0", 0, 0, &cs, &err))) {
        printf("compile fail: %s\n", err ? (char*)err->GetBufferPointer() : "?");
        return 1;
    }
    ID3D11ComputeShader* sh = nullptr;
    dev->CreateComputeShader(cs->GetBufferPointer(), cs->GetBufferSize(), nullptr, &sh);

    ctx->CSSetShader(sh, nullptr, 0);
    ID3D11ShaderResourceView* srvs[1] = { srv };
    ctx->CSSetShaderResources(0, 1, srvs);
    ID3D11UnorderedAccessView* uavs[1] = { uav };
    ctx->CSSetUnorderedAccessViews(0, 1, uavs, nullptr);
    ctx->CSSetConstantBuffers(0, 1, &pbuf);

    // 预热
    ctx->Dispatch(1024, 1, 1);
    ctx->CopyResource(canvas, canvas);
    auto t0 = std::chrono::high_resolution_clock::now();
    ctx->Dispatch(1024, 1, 1);
    // 强制同步：读回 1 个元素
    D3D11_BUFFER_DESC rbd = cb; rbd.Usage = D3D11_USAGE_STAGING; rbd.BindFlags = 0;
    rbd.MiscFlags = 0; rbd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ID3D11Buffer* rb = nullptr; dev->CreateBuffer(&rbd, nullptr, &rb);
    ctx->CopyResource(rb, canvas);
    D3D11_MAPPED_SUBRESOURCE ms{};
    ctx->Map(rb, 0, D3D11_MAP_READ, 0, &ms);
    ((uint32_t*)ms.pData)[0];
    ctx->Unmap(rb, 0);
    auto t1 = std::chrono::high_resolution_clock::now();
    double ms_t = std::chrono::duration<double, std::milli>(t1 - t0).count();
    double pix = (double)NS * 3.14159265 * 20.0 * 20.0;
    printf("stamps %d R=20 -> %.2f G pixel-ops in %.1f ms (%.1f G/s)\n", NS, pix / 1e9, ms_t, pix / ms_t / 1e6);
    return 0;
}
