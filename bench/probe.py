#!/usr/bin/env python3
"""同一リクエストの反復プローブ。

1問を同じ条件で N 回送り、応答の型（推論の有無）と正誤の分かれ方を記録する。
2026-11-01 の測定では、qwen3.5-flash の同一リクエスト40回のうち15回が
出力6トークンで即答し、その15回はすべて不正解だった。

  TEAI_API_KEY=... python3 bench/probe.py --model qwen/qwen3.5-flash-02-23 --task day-000 --n 40
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench.grade import grade  # noqa: E402
from bench.run import BASE, KEY, SYSTEM_RAG, Retriever, build_context, load_articles  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--task", default="day-000")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--no-rag", action="store_true", help="条文コンテキストを付けない")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    tasks = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8")}
    t = tasks[a.task]

    if a.no_rag:
        system, user = "あなたは日本の建築基準法に精通した建築士である。", t["question"]
    else:
        arts = load_articles()
        system = SYSTEM_RAG
        user = f"# 参考条文\n{build_context(t, Retriever(arts), arts)}\n\n# 質問\n{t['question']}"

    body = json.dumps(
        {
            "model": a.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": 12000,
            "temperature": 0,
        }
    ).encode()

    runs = []
    for i in range(a.n):
        req = urllib.request.Request(
            f"{BASE}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=300) as r:
            j = json.load(r)
        txt = j["choices"][0]["message"].get("content") or ""
        ok, _ = grade(t, txt)
        runs.append(
            {
                "run": i,
                "correct": ok,
                "pred": txt[:120],
                "completion_tokens": j["usage"]["completion_tokens"],
                "latency_s": round(time.time() - t0, 2),
            }
        )
        print(i, ok, runs[-1]["completion_tokens"], runs[-1]["latency_s"], repr(txt[:30]), flush=True)
        time.sleep(1.5)

    out = a.out or os.path.join(ROOT, "results", "probes", f"{a.model.replace('/', '_')}-{a.task}-repeat{a.n}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {
        "probe": "single-request-repeat" if not a.no_rag else "single-request-repeat-no-rag",
        "model": a.model,
        "protocol": f"同一リクエストを{a.n}回。temperature=0、max_tokens=12000" + ("、条文なし" if a.no_rag else "、RAG（同一の条文コンテキスト）"),
        "measured_at": time.strftime("%Y-%m-%d"),
        "tasks": [{"id": t["id"], "kind": t["kind"], "question": t["question"], "answer": t["answer"]}],
        "runs": runs,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    short = [r for r in runs if r["completion_tokens"] < 100]
    print(
        f"wrote {out}  n={len(runs)} correct={sum(1 for r in runs if r['correct'])} "
        f"short={len(short)} (short correct={sum(1 for r in short if r['correct'])}) "
        f"median_ctok={statistics.median(r['completion_tokens'] for r in runs):.0f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
