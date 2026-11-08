# 可选参考图（SDXS / Sana-Sprint / iMF）

## 0. 本机实测状态（2026-11-08）

- CUDA PyTorch `2.11.0+cu128`，`diffusers 0.42.1`，已装；
- **SDXS-512** 已下载（2.1GB），512×512、1 step 实测：首次含加载约 20s，第二次生成约 1.0s；
- **Sana-Sprint** 未安装：完整 Diffusers 目录约 7.7GB，本机下载过慢，接口保留但未实测；
- **iMF-B/2** 已下载 zip（约 330MB），但没有内置 JAX 加载器，`class_reference` 只能接 runner。

这一页是**可选**能力，不是凭空画入口，也不改变 plan/渲染的确定性。
参考图只用于让模型“看一眼”，不能描图，不能直接 vectorize 成 plan。

## 1. 环境

本机是 RTX 5070 Laptop 8GB，但 PyTorch 当前是 CPU 版。先装 CUDA 版：

~~~powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install diffusers transformers accelerate pillow
~~~

如果 cu128 轮子不可用，按 PyTorch 官网当前 Blackwell 对应版本安装；不要装 CPU 版然后指望 GPU。

## 2. Sana-Sprint 0.6B 与 SDXS（更小）

如果只要小模型，先用 **SDXS-512-DreamShaper**：约 **2.1 GB**，512px，一步生成，OpenRAIL++。

~~~powershell
python scripts/fetch_reference_models.py --download sdxs --dest models/sdxs-512-dreamshaper
$env:DSH_SDXS_PATH = "$PWD\models\sdxs-512-dreamshaper"
python scripts/reference_image.py sana --backend sdxs "..." out/ref.png --size 512 512 --steps 1
~~~

实测（RTX 5070 Laptop 8GB，SDXS-512，512×512，1 step）：首次含加载约 20s，第二次生成约 **1.0s**。

Sana 权重**不随仓库发布**：完整 Diffusers 目录约 **7.7 GB**（transformer 1.2 GB + VAE 1.25 GB + Gemma 文本编码器约 5.2 GB）。  
GitHub 普通仓库/单文件放不下，且 Sana 是 Apache-2.0、文本编码器 Gemma-2-2B 还受 Gemma Terms 约束；不要直接镜像。

从官方源下载：

~~~powershell
python scripts/fetch_reference_models.py --download sana --dest models/Sana_Sprint_0.6B_1024px_diffusers
$env:DSH_SANA_SPRINT_PATH = "$PWD\models\Sana_Sprint_0.6B_1024px_diffusers"
~~~

它下载的是
`Efficient-Large-Model/Sana_Sprint_0.6B_1024px_diffusers`，
不是只有 2.38 GB 的原始 .pth 仓库。

生成一张参考图：

~~~powershell
python scripts/reference_image.py sana --backend sdxs --model-path D:\DSHwork\flower\_models\sdxs-512-dreamshaper "a red fox standing on a mossy boulder at the edge of a misty forest at dawn" out/ref.png --size 512 512 --steps 1 --seed 0
~~~

Python：

~~~python
from reference_image import text_reference
meta = text_reference("...", "out/ref.png", size=(768, 768), steps=1, seed=0)
~~~

8GB 建议：

- 先用 512 或 768，不要一上来 1024；
- offload=True 默认开启；
- OOM 就降 size，不要把模型硬塞进显存；
- 生成失败返回错误，不阻断主绘画流程。

## 3. ImageNet / iMF 类别先验

iMF 的 ImageNet 权重是 JAX/Flax 研究代码，不是 Diffusers pipeline。
本仓库不内置加载器；接口只接受本地 runner：

~~~python
from reference_image import class_reference

def runner(class_id, out_png, seed):
    ...  # 你自己的 iMF/JAX 推理，写一张图到 out_png
    return out_png

meta = class_reference(277, "out/prior", n=4, seed=0, runner=runner)
~~~

277 是 ImageNet 的 red fox；278/279/280 分别是 kit/Arctic/grey fox。
类别图只提供“狐狸长什么样”的先验，不提供构图、姿态、支撑关系。

## 4. 许可证与发布

- Sana 权重：Apache-2.0（官方 repo 标注）；
- Gemma-2-2B-IT 文本编码器：Gemma Terms，需接受 Google 条款；
- iMF ImageNet 权重：模型卡没有许可证元数据，**不要随包/镜像发布**；
- 因此本仓库只发布：接口代码、官方源下载器、许可证说明；不发布权重。

## 5. 用途边界

- 生成构图/对象缩略图，给模型做视觉锚点；
- 让模型对比自己的渲染；
- 不描图；
- 不把参考图 vectorize 后当 plan；
- 不用参考图替代几何/接地判断。

## 6. 接口

~~~python
status() -> dict
text_reference(prompt, out_png, *, model_path=None, size=(768, 768),
              steps=1, seed=0, device="auto", offload=True,
              guidance_scale=0.0) -> dict
class_reference(class_id, out_dir, *, n=1, seed=0, runner=None) -> dict
~~~

没有模型/依赖时抛错或 available=False，不自动下载。
