"""视觉 grounding 的离线契约测试（不联网）。

    python tests/test_vision_grounding.py

断言：
  * build_messages：纯文本 -> content 字符串；带图 -> text + image_url 两个块
  * load_image_data_url：压到 max_side 内，data URL 可解码回图片
  * _chat_body：temperature=0，只有给图时才带 image 块
  * render_thumb：能从计划渲出非全黑缩略图（CPU 路径）
"""
import base64
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import numpy as np
from PIL import Image
import compile_ops


def test_messages():
    m0 = compile_ops.build_messages("hi")
    assert isinstance(m0[0]["content"], str)
    m1 = compile_ops.build_messages("hi", "data:image/png;base64,AAAA")
    assert isinstance(m1[0]["content"], list) and len(m1[0]["content"]) == 2
    assert m1[0]["content"][0]["type"] == "text"
    assert m1[0]["content"][1]["type"] == "image_url"
    print("PASS messages: text-only string / with image -> text+image_url blocks")


def test_image_encoding(tmp):
    path = Path(tmp) / "a.png"
    Image.new("RGB", (2000, 1200), (200, 30, 30)).save(path)
    url = compile_ops.load_image_data_url(path, max_side=320)
    head, b64 = url.split(",", 1)
    assert head == "data:image/png;base64", head
    im = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(im.size) <= 320, im.size
    print("PASS image encoding: 2000x1200 -> %dx%d data URL" % im.size)


def test_body():
    body = compile_ops._chat_body("m", "p", "data:image/png;base64,AAAA")
    assert body["temperature"] == 0
    assert body["messages"][0]["content"][1]["type"] == "image_url"
    body2 = compile_ops._chat_body("m", "p", None)
    assert body2["messages"][0]["content"] == "p"
    print("PASS chat body: temperature=0, image block only when image given")


def test_render_thumb(tmp):
    import plan_io
    plan = Path(tmp) / "p.json"
    strokes = [{"layer": "base", "preset": "round_soft", "size": 8.0, "opacity": 0.9,
                "color": [220, 40, 40], "points": [[6, 6, 1.0], [34, 30, 1.0]]}]
    plan_io.dump_plan(plan, 40, 32, strokes, {"base": "normal"})
    out = Path(tmp) / "thumb.png"
    im = compile_ops.render_thumb(plan, out, max_side=64, gpu=False)
    assert out.exists() and max(im.size) <= 64
    arr = np.asarray(im)
    assert arr.max() > 20, "thumb is all black"
    print("PASS render_thumb: %dx%d from a tiny plan (gpu=False)" % im.size)


def main():
    with tempfile.TemporaryDirectory() as td:
        test_messages()
        test_image_encoding(td)
        test_body()
        test_render_thumb(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
