// D3D11 compute probe: device + runtime-compiled CS + dispatch + readback
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi.h>
#include <cstring>
#include <cstdio>
#include <vector>

static const char* kSrc =
    "RWStructuredBuffer<float> buf : register(u0);\n"
    "[numthreads(64,1,1)]\n"
    "void main(uint3 id : SV_DispatchThreadID) { buf[id.x] = buf[id.x] * 2.0f + 1.0f; }\n";

int main() {
    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    D3D_FEATURE_LEVEL fl = D3D_FEATURE_LEVEL_11_0;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                   D3D11_SDK_VERSION, &dev, &fl, &ctx);
    if (FAILED(hr)) { printf("device fail %08lx\n", (unsigned long)hr); return 1; }
    IDXGIDevice* dxdev = nullptr;
    dev->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxdev);
    IDXGIAdapter* ad = nullptr; dxdev->GetAdapter(&ad);
    DXGI_ADAPTER_DESC desc{}; ad->GetDesc(&desc);
    printf("adapter: %ls\n", desc.Description);

    const int N = 4096;
    std::vector<float> host(N);
    for (int i = 0; i < N; ++i) host[i] = (float)i;

    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = N * sizeof(float);
    bd.Usage = D3D11_USAGE_DEFAULT;
    bd.BindFlags = D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE;
    bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = sizeof(float);
    D3D11_SUBRESOURCE_DATA sd{}; sd.pSysMem = host.data();
    ID3D11Buffer* buf = nullptr;
    dev->CreateBuffer(&bd, &sd, &buf);

    D3D11_UNORDERED_ACCESS_VIEW_DESC uavd{};
    uavd.Format = DXGI_FORMAT_UNKNOWN;
    uavd.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    uavd.Buffer.NumElements = N;
    ID3D11UnorderedAccessView* uav = nullptr;
    dev->CreateUnorderedAccessView(buf, &uavd, &uav);

    ID3DBlob* cs = nullptr; ID3DBlob* err = nullptr;
    hr = D3DCompile(kSrc, strlen(kSrc), nullptr, nullptr, nullptr, "main", "cs_5_0", 0, 0, &cs, &err);
    if (FAILED(hr)) { printf("compile fail: %s\n", err ? (char*)err->GetBufferPointer() : "?"); return 1; }
    ID3D11ComputeShader* shader = nullptr;
    dev->CreateComputeShader(cs->GetBufferPointer(), cs->GetBufferSize(), nullptr, &shader);

    ctx->CSSetShader(shader, nullptr, 0);
    ctx->CSSetUnorderedAccessViews(0, 1, &uav, nullptr);
    ctx->Dispatch(N / 64, 1, 1);

    ID3D11Buffer* rb = nullptr;
    bd.Usage = D3D11_USAGE_STAGING; bd.BindFlags = 0; bd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    dev->CreateBuffer(&bd, nullptr, &rb);
    ctx->CopyResource(rb, buf);
    D3D11_MAPPED_SUBRESOURCE ms{};
    ctx->Map(rb, 0, D3D11_MAP_READ, 0, &ms);
    float* out = (float*)ms.pData;
    printf("host[0..3] = %.1f %.1f %.1f %.1f\n", host[0], host[1], host[2], host[3]);
    printf("gpu[0..3]  = %.1f %.1f %.1f %.1f  gpu[4095]=%.1f\n", out[0], out[1], out[2], out[3], out[N-1]);
    ctx->Unmap(rb, 0);
    printf("OK\n");
    return 0;
}
