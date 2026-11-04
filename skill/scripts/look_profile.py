"""外观（look）配置：把"选中的参数"放成数据文件，而不是代码常量。

优先级（高 → 低）：
    1. 命令行 --ops <file>            单次实验，可重复，按顺序作用
    2. --look <file>                  本次指定的 profile
    3. scripts/native/look.json       仓库默认 profile（改了即生效，无需改代码）
    4. 恒等（gain=1 / lift=0 / gamma=1，逐字节不变）

profile 结构（缺省字段按恒等处理）：
    {"tone": {"gain": 1.0, "lift": 0.0, "gamma": 1.0}, "ops": [ ...任意算子... ],
     "notes": "为什么是这样"}
拿到参考图想重新标定，不要手改 gain —— 用 calibrate_tone.py 以目标图为基准求值，再把结果写回 profile。
"""
import json
from pathlib import Path

DEFAULT_PROFILE = Path(__file__).resolve().parent / "native" / "look.json"
NEUTRAL_TONE = {"gain": 1.0, "lift": 0.0, "gamma": 1.0}


def load_profile(path=None):
    """读 profile；文件不存在返回 None（= 恒等）。"""
    p = Path(path) if path else DEFAULT_PROFILE
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        prof = json.load(f)
    prof.setdefault("_source", str(p))
    return prof


def ops_from(profile):
    """把 profile 展开成算子列表；tone 全为恒等时不产生算子。"""
    if not profile:
        return []
    out = []
    tone = dict(NEUTRAL_TONE)
    tone.update(profile.get("tone") or {})
    if any(abs(float(tone[k]) - NEUTRAL_TONE[k]) > 1e-9 for k in NEUTRAL_TONE):
        spec = {"op": "tone_map"}
        spec.update({k: float(v) for k, v in tone.items()})
        out.append(spec)
    out.extend(profile.get("ops") or [])
    return out


def resolve(cli_ops=None, look_path=None):
    """按优先级合成算子列表：profile 在前，命令行 --ops 在后（覆盖式追加）。"""
    return ops_from(load_profile(look_path)) + list(cli_ops or [])
