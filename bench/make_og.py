#!/usr/bin/env python3
"""記事の共有画像（OGP 1200x630）を、保存済みの結果から作る。
  python3 bench/make_og.py --out ~/workspace/teai.io/web/og-kenchiku-benchmark.png
数字は results/sky-rag・sky-tools から読む（手で書かない）。Chrome のヘッドレス描画で PNG にする。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MODELS = [("openai/gpt-5.6-luna", "GPT-5.6 Luna"), ("google/gemini-3.5-flash", "Gemini 3.5 Flash"),
          ("openai/gpt-5.4-nano", "GPT-5.4 Nano")]


def summary(tag: str) -> dict:
    return {x["model"]: x for x in json.load(open(os.path.join(ROOT, "results", tag, "summary.json")))}


def html() -> str:
    rag, tools = summary("sky-rag"), summary("sky-tools")
    rows = []
    for m, label in MODELS:
        h, t = rag[m]["accuracy"] * 100, tools[m]["accuracy"] * 100
        rows.append(f"""<div class="row"><div class="lab">{label}</div>
<div class="bars"><div class="bar hand" style="width:{h:.0f}%"><span>手計算 {h:.0f}%</span></div>
<div class="bar tool" style="width:{t:.0f}%"><span>計算ツールあり {t:.0f}%</span></div></div></div>""")
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0}}
html,body{{width:1200px;height:630px;overflow:hidden}}
body{{background:#090f13;color:#eef6f2;font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif;position:relative;padding:48px 56px}}
.circle{{position:absolute;right:-180px;top:-140px;width:720px;height:720px;border-radius:50%;background:#12241c}}
.z{{position:relative}}
.eyebrow{{font-family:ui-monospace,Menlo,monospace;color:#a4f0bd;font-size:19px;letter-spacing:.06em}}
h1{{font-size:56px;line-height:1.22;font-weight:800;margin:16px 0 30px;letter-spacing:-.01em}}
.cap{{color:#b4c1c6;font-size:20px;margin-bottom:12px}}
.row{{display:grid;grid-template-columns:200px 1fr;align-items:center;gap:16px;margin:10px 0}}
.lab{{font-size:20px;color:#dfe9e4}}
.bars{{display:grid;gap:6px}}
.bar{{height:30px;border-radius:6px;display:flex;align-items:center;min-width:150px}}
.bar span{{font-size:16px;font-weight:700;padding:0 10px;white-space:nowrap}}
.hand{{background:#e06c6c;color:#1a0a0a}}
.tool{{background:#3f9b63;color:#04120b}}
.foot{{position:absolute;left:56px;bottom:40px;font-size:19px;color:#b4c1c6}}
.foot b{{color:#a4f0bd;font-family:ui-monospace,Menlo,monospace;font-weight:600}}
</style></head><body><div class="circle"></div><div class="z">
<div class="eyebrow">MEASURED · 建築基準法203問 ＋ 天空率50問</div>
<h1>建築法規をAIに聞くなら、<br>条文は貼り、計算は道具に。</h1>
<div class="cap">天空率50問の正解率（条文あり）</div>
{''.join(rows)}
</div><div class="foot"><b>teai.io/blog/kenchiku-benchmark</b>　立場別の使い方と、天空率の計算ツール（無料）</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "og.html")
        open(p, "w", encoding="utf-8").write(html())
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--window-size=1200,630", f"--screenshot={os.path.abspath(os.path.expanduser(a.out))}",
                        "file://" + p], check=True, capture_output=True, timeout=60)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
