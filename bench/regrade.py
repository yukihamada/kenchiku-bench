#!/usr/bin/env python3
"""保存済みの回答（pred）を現行の採点ロジックで再採点する。APIは叩かない。

採点基準を変えたときに、過去の実測を無駄にせず比較し直すため。既定では
results/ を書き換えず、results-regraded/ に出す（元データを壊さない）。

  python3 bench/regrade.py --tag rag
  python3 bench/regrade.py --tag rag --out results-regraded --in-place

注意: run.py は pred を 600 文字で切り詰めて保存する（run.py:392）。
応用計算（applied）など答えが末尾に来る種別は、切り詰められた pred を
再採点すると誤判定する。そのため pred が上限長に達している行は再採点せず、
保存時の correct をそのまま残す（--force で強制再採点）。
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

PRED_CAP = 600  # run.py の保存切り詰め長


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--tasks", default=os.path.join(ROOT, "data", "tasks.jsonl"))
    ap.add_argument("--out", default="results-regraded",
                    help="出力先ディレクトリ（results/ からの相対）。--in-place で results/ を直接更新")
    ap.add_argument("--in-place", action="store_true", help="results/<tag> を直接書き換える（非推奨）")
    ap.add_argument("--force", action="store_true", help="切り詰められた pred も強制的に再採点する")
    a = ap.parse_args()

    tasks = {t["id"]: t for t in (json.loads(l) for l in open(a.tasks, encoding="utf-8"))}
    src_dir = os.path.join(ROOT, "results", a.tag)
    dst_dir = src_dir if a.in_place else os.path.join(ROOT, a.out, a.tag)
    os.makedirs(dst_dir, exist_ok=True)

    out = []
    for path in sorted(glob.glob(os.path.join(src_dir, "*.jsonl"))):
        rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        changed = skipped = 0
        for r in rows:
            if r.get("error"):
                continue
            t = tasks.get(r["id"])
            if not t:
                continue
            pred = r.get("pred") or ""
            if not a.force and len(pred) >= PRED_CAP:
                skipped += 1
                continue
            ok, why = grade(t, pred)
            if ok != r["correct"]:
                changed += 1
            r["correct"], r["why"] = ok, why
        with open(os.path.join(dst_dir, os.path.basename(path)), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        ok = [r for r in rows if not r.get("error")]
        by_kind = {}
        for r in rows:
            k = by_kind.setdefault(r["kind"], {"n": 0, "ok": 0})
            k["n"] += 1
            k["ok"] += bool(r["correct"])
        out.append({
            "model": rows[0].get("model") or os.path.basename(path)[:-6],
            "n": len(rows),
            "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 4),
            "errors": len(rows) - len(ok),
            "latency_median_s": round(statistics.median(r["latency_s"] for r in ok), 3) if ok else None,
            "cost_per_task_usd": round(sum(r.get("cost_usd") or 0 for r in ok) / len(ok), 6) if ok else None,
            "by_kind": {k: {"n": v["n"], "accuracy": round(v["ok"] / v["n"], 3)} for k, v in sorted(by_kind.items())},
            "regraded": changed,
            "skipped_truncated": skipped,
        })
        print(f"{out[-1]['model']:32} acc={out[-1]['accuracy']:.4f} changed={changed} skipped={skipped}")
    json.dump(out, open(os.path.join(dst_dir, "summary.json"), "w"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
