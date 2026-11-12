# Inbox

这里放**候选**，不是正式参考。

pattern 候选：`candidate.json` + `crop.png` + `snippet.py` + `metrics.json`。

driver 候选：`candidate.json` + `driver.py` + `reference.png` + `manifest.json`。

```powershell
python scripts/grow_library.py check examples/inbox/<id>
python scripts/grow_library.py promote examples/inbox/<id>
```

只有通过检查的候选才会进 `examples/patterns/`。
