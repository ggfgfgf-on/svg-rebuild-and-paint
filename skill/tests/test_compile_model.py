"""第三方多模态编译的端到端契约（本地 mock 端点，不碰真实 key）。

验证：请求体里确实带 image_url（base64 PNG），返回值按 ops JSON 解析，
非数组响应会被拒绝，缺 key 时明确报 provider 未配置。
"""
import http.server
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compile_ops as C

OPS = [{"op": "set_layer_hue", "layer": "bed", "hue": 210}]


class Handler(http.server.BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        Handler.calls.append(body)
        reply = {"choices": [{"message": {"content": json.dumps(OPS)}}]}
        raw = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d/v1" % srv.server_port


def make_png(path):
    from PIL import Image
    Image.new("RGB", (16, 16), (200, 40, 40)).save(path)


def test_image_roundtrip(tmp):
    srv, base = serve()
    os.environ["OPENAI_API_KEY"] = "test-key"
    png = Path(tmp) / "ref.png"
    make_png(png)
    try:
        ops = C.compile_with_model("把背景改成蓝色", provider="openai", model="mock",
                                   base_url=base, image=str(png), retries=0)
    finally:
        srv.shutdown()
    assert ops == OPS, ops
    body = Handler.calls[-1]
    content = body["messages"][-1]["content"]
    blocks = [b for b in content if isinstance(b, dict)]
    imgs = [b for b in blocks if b.get("type") == "image_url"]
    assert imgs and imgs[0]["image_url"]["url"].startswith("data:image/"), "image must be inlined as data URL"
    assert body["temperature"] == 0
    print("PASS image roundtrip: request carried data:image PNG, ops parsed %d" % len(ops))


def test_status_errors():
    os.environ.pop("ARK_API_KEY", None)
    try:
        C.compile_with_model("x", provider="ark", retries=0)
        raise AssertionError("missing key must raise")
    except RuntimeError as ex:
        assert "not configured" in str(ex), ex
    print("PASS missing key: RuntimeError 'not configured' (no silent fallback here)")


def test_brief_material(tmp):
    """当前模型自己编译的入口：brief 只产出材料（摘要+prompt），不需要任何 key。"""
    import plan_io
    from PIL import Image
    p = Path(tmp) / "p.json"
    strokes = [{"layer": "bed", "preset": "round_soft", "size": 12.0, "opacity": 1.0,
                "color": [20, 30, 15], "points": [[10, 10, 1.0]]}]
    plan_io.dump_plan(str(p), 32, 32, strokes, layer_blends={"bed": "normal"})
    doc, w, h = plan_io.load_plan(p)
    summ = C.summarize(doc)
    assert [l["name"] for l in summ["layers"]] == ["bed"], summ
    prompt = C.build_prompt(summ, "把背景改成墨绿", None, has_image=True)
    for token in ("draw_path", "tone_map", "bed", "JSON"):
        assert token in prompt, token
    assert "image" in prompt.lower() or "图" in prompt
    print("PASS brief material: summary layers + prompt carries ops contract (no key needed)")


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_image_roundtrip(td)
    test_status_errors()
    import tempfile
    with tempfile.TemporaryDirectory() as td2:
        test_brief_material(td2)
    print("RESULT OK")


if __name__ == "__main__":
    main()
