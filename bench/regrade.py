#!/usr/bin/env python3
"""保存済みの回答（pred）を現行の採点ロジックで再採点する。APIは叩かない。

採点基準を変えたときに、過去の実測を無駄にせず比較し直すため。
  python3 bench/regrade.py --tag rag
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from bench.grade import grade  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    a = ap.parse_args()

    tasks = {t["id"]: t for t in (json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8"))}
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "results", a.tag, "*.jsonl"))):
        rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        changed = 0
        for r in rows:
            if r.get("error"):
                continue
            t = tasks.get(r["id"])
            if not t:
                continue
            ok, why = grade(t, r.get("pred") or "")
            if ok != r["correct"]:
                changed += 1
            r["correct"], r["why"] = ok, why
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        ok = [r for r in rows if not r.get("error")]
        by_kind = {}
        for r in rows:
            k = by_kind.setdefault(r["kind"], {"n": 0, "ok": 0})
            k["n"] += 1
            k["ok"] += bool(r["correct"])
        out.append({
            # モデルIDは行データの値を使う。ファイル名は '/' を '_' に置換しているため
            # （実測: summary の model が openai_gpt-5.5 になり、run.py の出力と食い違った）。
            "model": rows[0].get("model") or os.path.basename(path)[:-6],
            "n": len(rows),
            "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 4),
            "errors": len(rows) - len(ok),
            "latency_median_s": round(statistics.median(r["latency_s"] for r in ok), 3) if ok else None,
            "cost_per_task_usd": round(sum(r.get("cost_usd") or 0 for r in ok) / len(ok), 6) if ok else None,
            "by_kind": {k: {"n": v["n"], "accuracy": round(v["ok"] / v["n"], 3)} for k, v in sorted(by_kind.items())},
            "regraded": changed,
        })
        print(f"{out[-1]['model']:32} acc={out[-1]['accuracy']:.4f} changed={changed}")
    json.dump(out, open(os.path.join(ROOT, "results", a.tag, "summary.json"), "w"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
