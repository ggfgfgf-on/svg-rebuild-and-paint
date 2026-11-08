# 可选参考图（Sana-Sprint / iMF）

这一页是**可选**能力，不是凭空画入口，也不改变 plan/渲染的确定性。
参考图只用于让模型“看一眼”，不能描图，不能直接 vectorize 成 plan。

## 1. 环境

本机是 RTX 5070 Laptop 8GB，但 PyTorch 当前是 CPU 版。先装 CUDA 版：

~~~powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install diffusers transformers accelerate pillow
~~~

如果 cu128 轮子不可用，按 PyTorch 官网当前 Blackwell 对应版本安装；不要装 CPU 版然后指望 GPU。

## 2. Sana-Sprint 0.6B

下载到本地目录，不会自动下载：

~~~powershell
hf download Efficient-Large-Model/Sana_Sprint_0.6B_1024px --local-dir models/Sana_Sprint_0.6B_1024px
$env:DSH_SANA_SPRINT_PATH = "$PWD\models\Sana_Sprint_0.6B_1024px"
~~~

生成一张参考图：

~~~powershell
python scripts/reference_image.py sana "a red fox standing on a mossy boulder at the edge of a misty forest at dawn" out/ref.png --size 768 768 --steps 1 --seed 0
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

## 4. 用途边界

- 生成构图/对象缩略图，给模型做视觉锚点；
- 让模型对比自己的渲染；
- 不描图；
- 不把参考图 vectorize 后当 plan；
- 不用参考图替代几何/接地判断。

## 5. 接口

~~~python
status() -> dict
text_reference(prompt, out_png, *, model_path=None, size=(768, 768),
              steps=1, seed=0, device="auto", offload=True,
              guidance_scale=0.0) -> dict
class_reference(class_id, out_dir, *, n=1, seed=0, runner=None) -> dict
~~~

没有模型/依赖时抛错或 available=False，不自动下载。
