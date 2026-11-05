# dsh-tools — 把 svg-rebuild-and-paint 暴露成 DSH 工具

一个极薄的 host 插件：**不新增任何管线**，只把技能里已有的命令行注册成工具，让模型从工具列表就能"看见"能力，而不是靠 description 里的触发词去猜。

## 暴露的四个工具

| 工具 | 入参 | 背后命令 | 实测 |
|---|---|---|---|
| `art_vectorize` | image, out, k?, ss? | `vectorize.py <img> <out>` | 240×180 测试图 → **4 条 path，0.6 s** |
| `art_paint` | plan, out, ops?, gpu? | `render_plan.py <plan> --out <png>` | `examples/demo.plan.json` → **26 KB PNG，0.29 s** |
| `art_edit_brief` | plan, instruction, out | `compile_ops.py brief <plan> "<指令>" --thumb` | 产出 prompt + 缩略图，交给会话模型写 ops（零 key） |
| `art_apply_ops` | plan, ops, out, gpu? | `paint_cli.py edit <plan> --ops <json> --out <png>` | `examples/demo.ops.json` → **81 笔命中，dirty_rect 138,6,472,319，0.13 s** |

## 为什么是工具而不是触发词

技能目录只给模型看 `name + description`，而且会被截断。触发词写得再多，也是在赌路由器读到了哪一段。工具是**结构化接口**：名字、参数、description 都在能力列表里，模型自己判断要不要用——不需要任何人预先列举"把花改成蓝色"这种具体句子。

## 安装

```powershell
# 1) 拷进 profile（@deepseek-ai/* 从 profile node_modules 解析）
Copy-Item -Recurse -Force skil\plugin\dsh-tools "$env:DSH_HOME\profiles\web\node_modules\@dsh-ext\svg-paint-tools"

# 2) 在 profiles/web/cordis.patch.yml 末尾追加
# - insert:
#     - id: svg-paint-tools
#       name: '@dsh-ext/svg-paint-tools'
#       config:
#         skillDir: '<你的技能目录>/svg-rebuild-and-paint'

# 3) 重启 DSH
```

卸载：删掉 `cordis.patch.yml` 里那三行（或 `- id` 整条目），重启即可；插件卸载不影响技能本体。

## 失败模式

- 技能目录写错：插件在挂载时打 `[svg-paint-tools] not mounted: …` 并**跳过注册**，不会拖垮 host。
- 命令非零退出：工具抛出带最后 6 行 stderr 的错误，不会静默返回空结果。
