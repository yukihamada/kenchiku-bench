#!/usr/bin/env python3
"""実測結果を teai.io の記事用に1つの JSON へまとめる。API は叩かない。

  python3 bench/export_teai.py --out ../teai.io/web/blog-assets/kenchiku-bench-20261101.json

収録するもの:
  manifest     実行条件（モデル・タグ・プロンプト・RAG 設定）と機械可読な指紋
  tasks        203問の全文（質問・正解・根拠条文）
  summaries    tag ごと・モデルごとの集計（保存済み summary.json をそのまま）
  results      tag ごと・モデルごとの全応答（採点理由つき）
  sources      生成元ファイルの SHA-256
  reproduce    第三者が同じ測定を再実行するためのコマンド

方針:
  正解は tasks の answer/answer_norm のみ。モデルの回答を正解にしない。
  集計値は保存済み summary.json を再計算せずそのまま載せ、記事と数字がずれないようにする。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 実行条件のうち、リポジトリ内のファイルから機械的に取れないもの。
CONFIG = {
    "temperature": 0,
    "max_tokens": 12000,
    "rag": True,
    "rag_max_chars": 6000,
    "retrieval": "BM25 + 見出し完全一致 + 引用の5-gram包含一致（外部依存なし）",
    "grader": "決定論的（LLM判定なし）",
    "graded_by": "bench/grade.py",
    "law_source": "e-Gov 法令API（建築基準法・同施行令・同施行規則の本則）",
    "law_articles": 851,
    "system_prompt": (
        "あなたは日本の建築基準法に精通した建築士である。"
        "建築基準法・同施行令・同施行規則の条文に基づいて正確に答える。"
    ),
}

TAGS = [
    ("base", "条文を渡さない（素の知識）"),
    ("rag", "条文を検索して渡す（RAG）"),
    ("base-repeat1", "条文なしの2回目（qwen のみ・再現性検証）"),
    ("rag-repeat1", "RAG の2回目（qwen のみ・再現性検証）"),
    ("rag-repeat2", "RAG の3回目（gpt-5.4-nano のみ・再現性検証）"),
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pred-chars", type=int, default=600)
    a = ap.parse_args()

    tasks = [json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8")]
    task_order = [t["id"] for t in tasks]

    tags_out = []
    for tag, label in TAGS:
        d = os.path.join(ROOT, "results", tag)
        if not os.path.isdir(d):
            continue
        summaries = {s["model"]: s for s in json.load(open(os.path.join(d, "summary.json"), encoding="utf-8"))}
        models = []
        for path in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
            rows = [json.loads(l) for l in open(path, encoding="utf-8")]
            model = rows[0]["model"] if rows else os.path.basename(path)[:-6]
            rows.sort(key=lambda r: task_order.index(r["id"]) if r["id"] in task_order else 10**9)
            models.append({
                "model": model,
                "summary": summaries.get(model),
                "responses": [
                    {
                        "id": r["id"],
                        "correct": bool(r.get("correct")),
                        "why": r.get("why"),
                        "pred": (r.get("pred") or "")[: a.pred_chars],
                        "latency_s": r.get("latency_s"),
                        "prompt_tokens": r.get("prompt_tokens"),
                        "completion_tokens": r.get("completion_tokens"),
                        "cost_usd": round(r["cost_usd"], 6) if r.get("cost_usd") is not None else None,
                    }
                    for r in rows
                ],
            })
        tags_out.append({"tag": tag, "label": label, "models": models})

    sources = {}
    for rel in ["bench/tasks.py", "bench/grade.py", "bench/run.py", "data/tasks.jsonl", "corpus/fetch_laws.py"]:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            sources[rel] = sha256(p)

    probes = []
    probe_dir = os.path.join(ROOT, "results", "probes")
    if os.path.isdir(probe_dir):
        for name in sorted(os.listdir(probe_dir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(probe_dir, name), encoding="utf-8") as f:
                pr = json.load(f)
            runs = pr["runs"]
            probes.append(
                {
                    "file": name,
                    "probe": pr.get("probe", ""),
                    "model": pr.get("model", ""),
                    "protocol": pr.get("protocol", ""),
                    "tasks": [{"id": t["id"], "kind": t["kind"], "question": t["question"], "answer": t["answer"]}
                              for t in pr.get("tasks", [])] or (
                        [{"id": pr.get("task", ""), "kind": pr.get("kind", ""), "question": pr.get("question", ""),
                          "answer": pr.get("answer", "")}] if pr.get("task") else []
                    ),
                    "n": len(runs),
                    "correct": sum(1 for r in runs if r["correct"]),
                    "runs": runs,
                }
            )

    payload = {
        "manifest": {
            "event": "kenchiku-bench",
            "generated_at": "2026-11-01",
            "benchmark": "建築基準法203問",
            "tasks": len(tasks),
            "by_kind": {},
            "config": CONFIG,
            "fingerprint": hashlib.sha256(
                json.dumps([CONFIG, [t["id"] for t in tasks]], sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
        },
        "tasks": [
            {
                "id": t["id"],
                "kind": t["kind"],
                "topic": t["topic"],
                "question": t["question"],
                "answer": t["answer"],
                "answer_norm": t["answer_norm"],
                "evidence": t["evidence"],
            }
            for t in tasks
        ],
        "runs": tags_out,
        "probes": probes,
        "sources": sources,
        "reproduce": {
            "repo": "https://github.com/yukihamada/kenchiku-bench",
            "repo_visibility": "private",
            "code_availability": "採点コードは非公開リポジトリ。このJSONには203問・全応答・採点理由・実行コマンドを収録。",
            "steps": [
                "export TEAI_API_KEY=...（https://api.teai.io/v1）",
                "python3 corpus/fetch_laws.py     # e-Gov から条文を取得（取得済みなら不要）",
                "python3 bench/tasks.py           # 203問を決定論的に生成",
                "python3 bench/selftest.py        # API を叩かない自己テスト",
                "python3 bench/run.py --models <model> --tag mine",
                "python3 bench/run.py --models <model> --rag --tag mine-rag",
                "# 同一リクエストの反復（bench/probe.py 相当）: 1問を N 回送り、応答の型と正誤の分かれ方を記録",
                "MODEL=<model> TASK=day-000 N=40 OUT=results/probes/mine.json python3 bench/probe.py",
            ],
        },
    }

    kinds: dict[str, int] = {}
    for t in tasks:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    payload["manifest"]["by_kind"] = dict(sorted(kinds.items()))

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"wrote {a.out}  {len(body.encode()) / 1e6:.2f} MB")
    for tag in tags_out:
        print(f"  {tag['tag']:12} " + ", ".join(f"{m['model']}={m['summary']['accuracy']:.4f}" for m in tag['models']))
    return 0


if __name__ == "__main__":
    sys.exit(main())
