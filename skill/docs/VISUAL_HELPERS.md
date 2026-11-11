# 可选视觉辅助（SDXS / iMF）

## 0. 默认不调用

默认无参考凭空画：

- 不 import reference_image.py；
- 不加载 SDXS / iMF；
- 不下载权重；
- 不读取任何图片。

这些 helper 只有在用户明确允许外部视觉辅助时才调用。它们只返回 advisory 图片/元数据，不写 plan、不渲染 plan、不能描图或 vectorize。

参考图只支持 SDXS img2img。Sana-Sprint 已移除：本机未安装，pipeline 也没有 image 入口，不再维护。

## 这些函数是干嘛的

| 函数 | 输入 | 输出 | 用途 |
|---|---|---|---|
| text_reference | 一句英文/中文场景描述 | advisory PNG + 元数据 | 生成构图/氛围缩略图 |
| image_reference | 参考图 + 一句提示词 | advisory PNG + 元数据 | SDXS img2img，按参考图生成变体 |
| class_reference | ImageNet class_id（0–999） | 多张类别图 + 元数据 | 对象先验，例如狐狸长什么样 |
| imf_runner.generate | class_id + repo/ckpt | 256px 类别图 | class_reference 的实际 iMF runner |
| status | — | 依赖/模型路径状态 | 判断能不能用，不加载模型 |

它们不负责：写 plan、生成几何、分层、接地、审美判断。输出不能描图，也不能直接 vectorize 成 plan。

## 本机实测状态

- PyTorch CUDA 2.11.0+cu128，diffusers 0.42.1；
- SDXS-512 已下载（2.1GB），512×512、1 step 文生图实测；
- SDXS img2img 已实测：512×512 / strength 0.45 / 4 steps；
- iMF-B/2 已下载（约 356MB），PyTorch 分支 256px class 277 实测可跑。

## 1. 环境

~~~powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install diffusers transformers accelerate pillow
~~~

如果 cu128 轮子不可用，按 PyTorch 官网当前 Blackwell 对应版本安装；不要装 CPU 版然后指望 GPU。

## 2. SDXS：文生图与参考图

下载：

~~~powershell
python scripts/fetch_reference_models.py --download sdxs --dest models/sdxs-512-dreamshaper
$env:DSH_SDXS_PATH = "$PWD\models\sdxs-512-dreamshaper"
~~~

文生图：

~~~powershell
python scripts/reference_image.py text "a red fox at dawn" out/ref.png --backend sdxs --model-path D:\DSHwork\flower\_models\sdxs-512-dreamshaper --size 512 512 --steps 1 --seed 0
~~~

参考图 img2img：

~~~powershell
python scripts/reference_image.py img2img in/ref.png "same subject, oil painting" out/ref_i2i.png --backend sdxs --model-path D:\DSHwork\flower\_models\sdxs-512-dreamshaper --strength 0.45 --steps 4 --seed 0
~~~

| 参数 | 含义 |
|---|---|
| strength | 0.05 贴近参考图，0.95 更自由；默认 0.55 |
| steps | 1–8；steps * strength 必须 >= 1，否则没有有效去噪步 |
| size | 默认按参考图取 32 的倍数；显式给 512 512 等 |

低步数会明显发糊，做质感变体建议 4–8 步。

SDXS img2img 也是 advisory：输出不能直接 vectorize 成 plan，也不替代几何/接地判断。

## 3. ImageNet / iMF 类别先验

iMF 用官方 PyTorch 推理分支 + scripts/imf_runner.py，不需要 JAX。权重/仓库不随包：

~~~powershell
python scripts/fetch_reference_models.py --download imf-b2 --dest D:\models\imf
git clone --depth 1 --branch torch https://github.com/Lyy-iiis/imeanflow.git D:\models\imeanflow_torch
python scripts/imf_runner.py --repo D:\models\imeanflow_torch --ckpt D:\models\imf\iMF-B-2.pth --class-id 277 --out out/prior/fox.png
~~~

也可以接到 class_reference：

~~~python
from functools import partial
from reference_image import class_reference
from imf_runner import generate

runner = partial(generate, "D:/models/imeanflow_torch", "D:/models/imf/iMF-B-2.pth")
meta = class_reference(277, "out/prior", n=1, seed=0, runner=runner)
~~~

277 是 ImageNet 的 red fox。类别图只提供“狐狸长什么样”的先验，不提供构图、姿态、支撑关系。

## 3.1 模型 smoke

模型推理不跑默认 pytest；显式跑：

~~~powershell
python scripts/model_smoke.py --model sdxs --out out/smoke-sdxs
python scripts/model_smoke.py --model imf --repo <imeanflow_torch> --ckpt <iMF-B-2.pth> --out out/smoke-imf
~~~

SDXS smoke 会跑两次：文生图 + 拿文生图结果做 img2img。
模型测试默认 skip；DSH_MODEL_SMOKE=1 时才在 pytest 里跑。

## 4. 许可证与发布

- SDXS-512-DreamShaper：OpenRAIL++；
- iMF ImageNet 权重：模型卡没有许可证元数据，不要随包/镜像发布；
- 本仓库只发布接口代码、官方源下载器、许可证说明；不发布权重。

## 5. 用途边界

- 生成构图/对象缩略图，给模型做视觉锚点；
- 让模型对比自己的渲染；
- 不描图；
- 不把辅助图 vectorize 后当 plan；
- 不用辅助图替代几何/接地判断。

## 6. 接口

~~~python
status() -> dict
text_reference(prompt, out_png, *, model_path=None, size=(512, 512),
              steps=1, seed=0, device="auto", offload=True,
              guidance_scale=0.0, backend="sdxs") -> dict
image_reference(prompt, init_image, out_png, *, model_path=None, size=None,
                strength=0.55, steps=4, seed=0, device="auto",
                offload=True, guidance_scale=0.0, backend="sdxs") -> dict
class_reference(class_id, out_dir, *, n=1, seed=0, runner=None) -> dict
~~~

没有模型/依赖时抛错或 available=False，不自动下载。
