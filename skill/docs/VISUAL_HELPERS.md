# 可选视觉辅助（SDXS / Sana-Sprint / iMF）

## 0. 默认不调用

默认无参考凭空画：

- 不 import reference_image.py；
- 不加载 SDXS/Sana/iMF；
- 不下载权重；
- 不读取任何图片。

这些 helper 只有在用户明确允许外部视觉辅助时才调用。它们只返回 advisory 图片/元数据，不写 plan、不渲染 plan、不能描图或 vectorize。

**参考图只有 SDXS img2img 一条**：输入图 + 提示词 → advisory 图，仍然不是 plan。Sana-Sprint 的 pipeline 没有 `image` 入口，不能假装支持。

## 这些函数是干嘛的

| 函数 | 输入 | 输出 | 用途 |
|---|---|---|---|
| text_reference | 一句英文/中文场景描述 | advisory PNG + 元数据 | 生成构图/氛围缩略图，给模型看一眼 |
| image_reference | 参考图 + 一句提示词 | advisory PNG + 元数据 | SDXS img2img，按参考图生成变体 |
| class_reference | ImageNet class_id（0–999） | 多张类别图 + 元数据 | 对象先验，例如狐狸长什么样 |
| imf_runner.generate | class_id + repo/ckpt | 256px 类别图 | class_reference 的实际 iMF runner |
| status | — | 依赖/模型路径状态 | 判断能不能用，不加载模型 |

它们**不负责**：写 plan、生成几何、分层、接地、审美判断。输出不能描图，也不能直接 vectorize 成 plan。

| 后端 | 文生图 | 参考图 img2img | 本机状态 |
|---|---|---|---|
| SDXS-512 | 支持 | 支持 | 已实测 |
| Sana-Sprint | 支持 | 不支持 | 未装权重 |
| iMF-B/2 | 类别先验 | 不支持 | 已实测 |



## 职责边界

- 这是**可选视觉辅助**，不是 plan 输入；
- SDXS 可用参考图做 img2img，但输出仍只是 advisory 图；
- 只生成 advisory 图片/元数据，不写 plan、不生成几何、不判断接地；
- 默认无参考凭空画不 import、不下载、不读取；
- 只有用户明确允许外部视觉辅助时才调用；
- 失败/缺少权重不阻断主绘画流程。

## 0.1 本机实测状态

- CUDA PyTorch `2.11.0+cu128`，`diffusers 0.42.1`，已装；
- **SDXS-512** 已下载（2.1GB），512×512、1 step 实测：首次含加载约 20s，第二次生成约 1.0s；
- **SDXS img2img** 已实测：512×512 / strength 0.45 / 4 steps，参考图变体生成正常；steps * strength 必须 >= 1；
- **Sana-Sprint** 未安装：完整 Diffusers 目录约 7.7GB，本机下载过慢，接口保留但未实测；
- **iMF-B/2** 已下载 PyTorch `.pth`（约 356MB），用官方 torch 分支 + `imf_runner.py` **实测可跑**（256px，class 277，生成约 0.9s；首次加载约 20s）。

这一页是**可选视觉辅助（advisory prior）**，不进入默认凭空画流程。
它只生成给模型“看一眼”的辅助图；不能描图，不能直接 vectorize 成 plan。

## 1. 环境

本机是 RTX 5070 Laptop 8GB，PyTorch 为 CUDA 版（2.11.0+cu128）。如果换机器且只有 CPU 版，先装 CUDA 版：

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

生成一张辅助图：

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

## 2.1 参考图 img2img（只有 SDXS）

参考图不是 plan，也不是“描图输入”。它只帮模型先看一张构图/质感变体；最终几何仍要自己写。

~~~powershell
python scripts/reference_image.py img2img in/ref.png "same subject, oil painting" out/ref_i2i.png \
  --backend sdxs --model-path D:\DSHwork\flower\_models\sdxs-512-dreamshaper \
  --strength 0.45 --steps 4 --seed 0
~~~

| 参数 | 含义 |
|---|---|
| strength | 0.05 贴近参考图，0.95 更自由；默认 0.55 |
| steps | 1–8；steps * strength 必须 >= 1，否则没有有效去噪步 |
| size | 默认按参考图取 32 的倍数；显式给 512 512 等 |

实测：512×512 / strength 0.45 / 4 steps 可跑；低步数会明显发糊，做质感变体建议 4–8 步。

不支持 Sana-Sprint：它的 pipeline 没有 image 参数，硬接只能自己写 latents 和 SCM 噪声调度，当前没有可靠实现。需要参考图就用 SDXS，或换一个真正支持 img2img 的 Sana checkpoint。

SDXS img2img 也是 advisory：输出不能直接 vectorize 成 plan，也不替代几何/接地判断。

## 3. ImageNet / iMF 类别先验

iMF 现在用官方 **PyTorch 推理分支** + `scripts/imf_runner.py`，不需要 JAX。
权重/仓库不随包：

~~~powershell
python scripts/fetch_reference_models.py --download imf-b2 --dest D:\models\imf
git clone --depth 1 --branch torch https://github.com/Lyy-iiis/imeanflow.git D:\models\imeanflow_torch
python scripts/imf_runner.py --repo D:\models\imeanflow_torch --ckpt D:\models\imf\iMF-B-2.pth --class-id 277 --out out/prior/fox.png
~~~

`imf_runner` 会加载 VAE、模型和 `.pth`；首次加载较慢，之后 256px 一步约 1 秒。

也可以接到 `class_reference`：

~~~python
from functools import partial
from reference_image import class_reference
from imf_runner import generate

runner = partial(generate, "D:/models/imeanflow_torch", "D:/models/imf/iMF-B-2.pth")
meta = class_reference(277, "out/prior", n=1, seed=0, runner=runner)
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
- 不把辅助图 vectorize 后当 plan；
- 不用辅助图替代几何/接地判断。

## 6. 接口

~~~python
status() -> dict
text_reference(prompt, out_png, *, model_path=None, size=(768, 768),
              steps=1, seed=0, device="auto", offload=True,
              guidance_scale=0.0) -> dict
image_reference(prompt, init_image, out_png, *, model_path=None, size=None,
                strength=0.55, steps=4, seed=0, device="auto",
                offload=True, guidance_scale=0.0, backend="sdxs") -> dict
class_reference(class_id, out_dir, *, n=1, seed=0, runner=None) -> dict
~~~

没有模型/依赖时抛错或 available=False，不自动下载。
