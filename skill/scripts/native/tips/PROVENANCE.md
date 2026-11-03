# 笔刷素材来源与许可（scripts/native/tips/）

本目录下的 279 个 PNG（128×128，灰度）**不是商业素材**，全部由开源笔刷包转换而来。

## 来源

- 输入格式：Krita 笔刷（`.kpp`）、GIMP 笔刷（`.gbr` / `.gih`）、普通 PNG/灰度图
- 转换脚本：`scripts/native/import_tips.py`（统一规范化为 128×128 alpha 表）
- 每条索引的 `src` 字段记录原始文件名，见 `index.json`

## 许可

- 这些笔刷包随 Krita / GIMP 生态分发，**各自保留原许可证**（多为 GPL 或 CC-BY-SA，少数为作者自定义的允许再分发条款）。
- 本仓库为 GPL（根 `LICENSE` 为 GPLv3，`skill/LICENSE` 为 GPLv2），与上述许可证兼容；再分发时请保留本文件与 `index.json` 中的 `src` 记录。
- 若用于闭源产品，请先按 `index.json` 的 `src` 回溯对应笔刷包许可证，不要默认继承本仓库的 GPL。

## 复现

    python scripts/native/import_tips.py <笔刷目录或文件...>
