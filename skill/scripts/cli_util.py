"""脚本共用 CLI 约定：--gpu / --threads / --target-* 的一份实现。

之前每个脚本各写一遍 argparse 选项，名字和默认值容易漂移（比如有的用 --target-image，
有的把目标亮度塞进位置参数）。这里只做参数"形状"的统一，不掺业务逻辑。
"""
import argparse
import json
from pathlib import Path


def add_common_args(ap, gpu=True, threads=True, quiet=True):
    """给 parser 加上通用开关，返回同一个 parser（可链式调用）。"""
    if gpu:
        ap.add_argument("--gpu", action="store_true", help="用 GPU 渲染（DirectCompute），缺省 CPU")
    if threads:
        ap.add_argument("--threads", type=int, default=8, help="渲染线程数（默认 8）")
    if quiet:
        ap.add_argument("--quiet", action="store_true", help="不打印逐轮标定/重采样日志")
    return ap


def add_target_args(ap, required=True):
    """目标亮度来源：参考图或显式 luma，二选一（互斥组）。"""
    g = ap.add_mutually_exclusive_group(required=required)
    g.add_argument("--target-image", help="参考图路径：用它的平均亮度当目标")
    g.add_argument("--target-luma", type=float, help="直接给目标亮度（0-255）")
    return ap


def resolve_target(args, luma_fn):
    """返回目标亮度；两个都没给或都给了就报错，绝不默认成某个常数。"""
    has_img = getattr(args, "target_image", None) is not None
    has_luma = getattr(args, "target_luma", None) is not None
    if has_img == has_luma:
        raise SystemExit("need exactly one of --target-image / --target-luma")
    return luma_fn(args.target_image) if has_img else float(args.target_luma)


def write_json(path, obj, indent=1):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    return path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def log_unless_quiet(args, msg):
    if not getattr(args, "quiet", False):
        print(msg, flush=True)
