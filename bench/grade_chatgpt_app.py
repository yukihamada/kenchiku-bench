#!/usr/bin/env python3
"""drafts/chatgpt-app-10q.md に貼り戻された ChatGPT アプリの答えを採点し、results/chatgpt-app/summary.json に書く。
  python3 bench/grade_chatgpt_app.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bench.grade import grade  # noqa: E402


def main() -> int:
    md = open(os.path.join(ROOT, "drafts", "chatgpt-app-10q.md"), encoding="utf-8").read()
    ids = json.load(open(os.path.join(ROOT, "drafts", "chatgpt-app-10q.ids.json")))
    tasks = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8")}
    model = (re.search(r"^MODEL:\s*(.*)$", md, re.M) or [None, ""])[1].strip()
    rows = []
    for i, tid in enumerate(ids, 1):
        m = re.search(rf"^ANSWER_{i}:\s*\n(.*?)(?=^---|\Z)", md, re.M | re.S)
        ans = (m.group(1) if m else "").strip()
        if not ans:
            rows.append({"id": tid, "answered": False})
            continue
        ok, why = grade(tasks[tid], ans)
        rows.append({"id": tid, "answered": True, "correct": ok, "why": why, "pred": ans[:600]})
    done = [r for r in rows if r["answered"]]
    out = {"model": model, "answered": len(done), "correct": sum(r["correct"] for r in done), "rows": rows}
    os.makedirs(os.path.join(ROOT, "results", "chatgpt-app"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "results", "chatgpt-app", "summary.json"), "w"), ensure_ascii=False, indent=1)
    print(f"モデル: {model or '未記入'}  回答 {len(done)}/10  正解 {out['correct']}/{len(done)}")
    for r in rows:
        print(" ", r["id"], "未回答" if not r["answered"] else ("○" if r["correct"] else "×"), r.get("why", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
