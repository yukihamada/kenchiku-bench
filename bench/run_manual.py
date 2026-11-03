#!/usr/bin/env python3
"""「人が条文を貼る」使い方を測る（記事の使い方節の手順そのまま）。

- 貼る条文は記事の早見表どおり（建蔽率=法53条、容積率=法52条、採光補正係数=令20条、応用=法52条+53条）を丸ごと。
- 質問は記事の「そのまま貼れる質問の型」に、条文と元の問題文を入れたもの。system プロンプトなし（ChatGPT に貼る想定）。
- 計算系4種から各10問（seed固定）。比較対象は同じ問題の base（条文なし）と rag（自動検索）の保存済み結果。

  python3 bench/run_manual.py --models openai/gpt-5.5,openai/gpt-5.6-luna --tag manual
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from bench.grade import grade  # noqa: E402
from bench.run import chat, load_pricing  # noqa: E402

SHEET = {"calc_bcr": [("建築基準法", "53")], "calc_far": [("建築基準法", "52")],
         "calc_daylight": [("建築基準法施行令", "20")], "applied": [("建築基準法", "52"), ("建築基準法", "53")]}

TEMPLATE = ("次の条文だけを根拠に答えてください。\n【条文】\n{articles}\n【質問】\n{question}\n"
            "答え方：1行目に答えの数値、2行目に根拠の条番号、3行目に計算が要る場合は式を1行。\n"
            "条文に書いていないことは「条文に記載なし」と書く。式の結果は自分で検算する。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--tag", default="manual")
    ap.add_argument("--per-kind", type=int, default=10)
    a = ap.parse_args()
    arts = {}
    for l in open(os.path.join(ROOT, "corpus", "articles.jsonl"), encoding="utf-8"):
        d = json.loads(l)
        arts[(d["law"], d["article"])] = d["text"]
    tasks = [json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8")]
    rng = random.Random(20261103)
    pick = []
    for k in SHEET:
        pool = [t for t in tasks if t["kind"] == k]
        pick += rng.sample(pool, min(a.per_kind, len(pool)))
    pricing = load_pricing()
    out = os.path.join(ROOT, "results", a.tag)
    os.makedirs(out, exist_ok=True)

    def one(t, model):
        body = "\n\n".join(f"{law}第{art.replace('の', '条の') if 'の' in art else art + '条'}\n{arts[(law, art)]}"
                           for law, art in SHEET[t["kind"]])
        prompt = TEMPLATE.format(articles=body, question=t["question"])
        rec = {"id": t["id"], "kind": t["kind"], "model": model}
        try:
            r = chat(model, [{"role": "user", "content": prompt}], max_tokens=12000, retries=1)
        except Exception as e:
            rec.update({"correct": False, "error": str(e)[:300]})
            return rec
        ok, why = grade(t, r["text"])
        u = r["usage"] or {}
        p = pricing.get(model, {})
        rec.update({"correct": ok, "why": why, "pred": r["text"][:600], "latency_s": round(r["latency_s"], 2),
                    "prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
                    "cost_usd": (u.get("prompt_tokens", 0) * p.get("in", 0) + u.get("completion_tokens", 0) * p.get("out", 0)) / 1e6 if p else None})
        return rec

    summ = []
    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        with ThreadPoolExecutor(max_workers=2) as ex:
            rows = list(ex.map(lambda t: one(t, model), pick))
        with open(os.path.join(out, re.sub(r"[^A-Za-z0-9._-]", "_", model) + ".jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        n = len(rows)
        err = sum(1 for r in rows if r.get("error"))
        summ.append({"model": model, "n": n, "accuracy": sum(r["correct"] for r in rows) / n, "errors": err,
                     "ids": [r["id"] for r in rows]})
        print(model, summ[-1]["accuracy"], "errors", err, flush=True)
    json.dump(summ, open(os.path.join(out, "summary.json"), "w"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
