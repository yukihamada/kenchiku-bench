#!/usr/bin/env python3
"""kenchiku-bench ランナー。teai (OpenAI互換) 経由で複数モデルを実測する。

使い方:
  python3 bench/run.py --models gpt-4.1-nano,claude-haiku-5-5 --limit 20
  python3 bench/run.py --models google/gemini-3.5-flash --rag --tag rag
  python3 bench/run.py --models gpt-4.1-nano --kind calc_daylight
  python3 bench/run.py --models openai/gpt-5.6-luna --tasks data/tasks_sky.jsonl --rag --tools --tag sky-tools

出力: results/<tag>/<model>.jsonl （1行1タスク、採点・レイテンシ・usage入り）
       results/<tag>/summary.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from bench.grade import grade  # noqa: E402
from bench.sky import measurement_points, sky_ratio  # noqa: E402

# --tools のときモデルに渡す計算ツール（天空率は条文を読むだけでは答えが出ないため）
TOOLS = [
    {"type": "function", "function": {
        "name": "sky_ratio",
        "description": "建築基準法施行令第135条の5の天空率を計算する。建物は直方体の集まり。"
                       "座標はm。h と z は同じ基準面からの高さ。戻り値は天空率（％）。",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "number", "description": "測定点のx"},
            "y": {"type": "number", "description": "測定点のy"},
            "z": {"type": "number", "description": "測定点の高さ（boxes の h と同じ基準。省略時0）"},
            "boxes": {"type": "array", "items": {"type": "object", "properties": {
                "x0": {"type": "number"}, "x1": {"type": "number"},
                "y0": {"type": "number"}, "y1": {"type": "number"},
                "h": {"type": "number", "description": "高さm"}},
                "required": ["x0", "x1", "y0", "y1", "h"]}}},
            "required": ["x", "y", "boxes"]}}},
    {"type": "function", "function": {
        "name": "sky_measurement_points",
        "description": "令第135条の9第1項の道路高さ制限の測定点の最少数と間隔を返す（直線の前面道路1本）。",
        "parameters": {"type": "object", "properties": {
            "frontage": {"type": "number", "description": "敷地が前面道路に面する長さm"},
            "road_width": {"type": "number", "description": "前面道路の幅員m"}},
            "required": ["frontage", "road_width"]}}},
]


def call_tool(name: str, args: dict) -> str:
    try:
        if name == "sky_ratio":
            boxes = [{k: float(b[k]) for k in ("x0", "x1", "y0", "y1", "h")} for b in args["boxes"]]
            return json.dumps({"sky_ratio_pct": round(sky_ratio(float(args["x"]), float(args["y"]), boxes,
                                                                pz=float(args.get("z") or 0)) * 100, 3)})
        if name == "sky_measurement_points":
            n, gap = measurement_points(float(args["frontage"]), float(args["road_width"]))
            return json.dumps({"points": n, "spacing_m": round(gap, 4)})
    except Exception as e:  # 引数の誤りはモデルに返して直させる
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    return json.dumps({"error": f"unknown tool {name}"})

TASKS = os.path.join(ROOT, "data", "tasks.jsonl")
ARTICLES = os.path.join(ROOT, "corpus", "articles.jsonl")
PRICING_CACHE = os.path.join(ROOT, "data", "pricing.json")

BASE = os.environ.get("TEAI_BASE_URL", "https://api.teai.io/v1")
KEY = os.environ.get("TEAI_API_KEY", "")

SYSTEM = (
    "あなたは日本の建築基準法に精通した建築士である。"
    "建築基準法・同施行令・同施行規則の条文に基づいて正確に答える。"
    "推測で数値を書かず、条文に無い値は「不明」と述べる。"
    "指定された形式（数値のみ／分数／条番号）を厳守し、余計な前置きを書かない。"
)

SYSTEM_RAG = (
    SYSTEM
    + "\n\n参考条文が与えられた場合は、必ずその条文の記述を根拠に答えよ。"
      "参考条文に答えが無い場合は、自分の知識で補ってよいが、その旨を明示せよ。"
)


# ---------------------------------------------------------------- 価格表

def load_pricing() -> dict:
    if os.path.exists(PRICING_CACHE):
        return json.load(open(PRICING_CACHE))
    req = urllib.request.Request(f"{BASE}/models/pricing", headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)["data"]
    out = {m["id"]: {"in": m["input_per_1m_tokens"], "out": m["output_per_1m_tokens"],
                     "ctx": m.get("context_window")} for m in d}
    os.makedirs(os.path.dirname(PRICING_CACHE), exist_ok=True)
    json.dump(out, open(PRICING_CACHE, "w"), ensure_ascii=False, indent=0)
    return out


# ---------------------------------------------------------------- 検索（RAG）

def load_articles() -> list[dict]:
    return [json.loads(l) for l in open(ARTICLES, encoding="utf-8")]


def _terms(s: str) -> set[str]:
    """日本語の簡易トークン化。漢字2-gram + カタカナ語 + 数字。"""
    out = set()
    for m in re.finditer(r"[一-龥]{2,}", s):
        t = m.group(0)
        for i in range(len(t) - 1):
            out.add(t[i:i + 2])
    for m in re.finditer(r"[ァ-ヴー]{2,}", s):
        out.add(m.group(0))
    for m in re.finditer(r"[A-Za-z]{2,}", s):
        out.add(m.group(0).lower())
    return out


class Retriever:
    """BM25 + 見出し重み + 文字n-gram一致の簡易ハイブリッド検索。

    外部依存なしで動くことを優先。実測: 素のBM25（本文のみ）は recall@3 が
    0.578 で、条文同定タスクでは見出し語が本文に薄くしか出ないため落ちていた。
    見出しを3倍・5-gram一致を加点して改善する。
    """

    NGRAM = 5

    def __init__(self, articles: list[dict]):
        self.docs = articles
        # 見出し（caption）は本文に比べて極端に短いので、3回繰り返して重みを上げる
        self.doc_terms = [_terms((a["caption"] + " ") * 3 + a["text"]) for a in articles]
        self.doc_ngrams = [_ngrams(a["caption"] + a["text"], self.NGRAM) for a in articles]
        self.df: dict[str, int] = {}
        for ts in self.doc_terms:
            for t in ts:
                self.df[t] = self.df.get(t, 0) + 1
        self.N = len(articles)
        self.avgdl = sum(len(t) for t in self.doc_terms) / max(1, self.N)
        # 見出し（括弧内）の正規化形。条文同定では見出し語がそのまま質問に来るので
        # 完全一致を強く加点する（実測: 「防火壁等」の質問で第26条が6位以下だった）。
        self.captions = [re.sub(r"[（）()\s]", "", a["caption"]) for a in articles]

    def search_containment(self, passage: str, k: int = 2) -> list[dict]:
        """長い引用文がそのまま条文に含まれるかを5-gram一致率で探す。

        誤り検出タスクは条文の一節をそのまま引用するので、BM25（語の重み）より
        包含一致の方が確実に当たる（実測: BM25 は false_claim で 17/23、
        包含一致は 23/23）。
        """
        qg = _ngrams(passage, self.NGRAM)
        if len(qg) < 8:
            return []
        scored = []
        for i, dg in enumerate(self.doc_ngrams):
            if not dg:
                continue
            inter = len(qg & dg)
            if inter:
                scored.append((inter / len(qg), i))
        scored.sort(reverse=True)
        return [self.docs[i] for s, i in scored[:k] if s >= 0.5]

    def search(self, query: str, k: int = 3) -> list[dict]:
        import math
        q = _terms(query)
        qg = _ngrams(query, self.NGRAM)
        qn = re.sub(r"[（）()\s]", "", query)
        # 質問文に法令名が書かれていれば、その法令の条文を優先する
        law_pref = ""
        for cand in ("建築基準法施行規則", "建築基準法施行令", "建築基準法"):
            if cand in query:
                law_pref = cand
                break
        scored = []
        for i, ts in enumerate(self.doc_terms):
            if not ts:
                continue
            s = 0.0
            dl = len(ts)
            for t in q:
                if t not in ts:
                    continue
                idf = math.log(1 + (self.N - self.df.get(t, 0) + 0.5) / (self.df.get(t, 0) + 0.5))
                s += idf * 2.2 / (1 + 1.2 * (0.25 + 0.75 * dl / self.avgdl))
            if qg:
                dg = self.doc_ngrams[i]
                inter = len(qg & dg)
                if inter:
                    s += 3.0 * inter / len(qg)
            cap = self.captions[i]
            if cap and len(cap) >= 2 and cap in qn:
                s += 12.0 + 2.0 * len(cap)
            if law_pref and self.docs[i]["law"] == law_pref:
                s *= 1.5
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [self.docs[i] for _, i in scored[:k]]


def _ngrams(s: str, n: int) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


ARTICLE_REF = re.compile(r"第\s*([0-9〇一二三四五六七八九十百千]+(?:の[0-9一二三四五六七八九十]+)?)\s*条")


def resolve_refs(query: str, articles: list[dict]) -> list[dict]:
    """質問文に「第52条」等の条番号参照があれば、その条文を直接引く。

    実務の建築士は条文番号から引くので、これは現実の使い方に沿う。
    法令名は質問文の「建築基準法施行令」等から推定し、無指定なら法を優先。
    """
    refs = set()
    for m in ARTICLE_REF.finditer(query):
        a = m.group(1)
        a = a.replace("の二", "の2").replace("の三", "の3").replace("の四", "の4")
        mm = re.fullmatch(r"([〇一二三四五六七八九十百千]+)(の\d+)?", a)
        if mm:
            from bench.grade import kanji_num
            n = kanji_num(mm.group(1))
            if n is not None:
                a = f"{n}{mm.group(2) or ''}"
        refs.add(a)
    if not refs:
        return []
    law = "建築基準法施行令" if "施行令" in query else "建築基準法"
    out = []
    for a in sorted(refs):
        for r in articles:
            if r["law"] == law and r["article"] == a:
                out.append(r)
                break
    return out


QUOTED = re.compile(r"「([^」]{30,})」")


def quoted_passage(question: str) -> str:
    m = QUOTED.search(question)
    return m.group(1) if m else ""


def rag_context(hits: list[dict], max_chars: int = 6000) -> str:
    """条文を渡された順に、上限まで詰める。

    段落単位で関連箇所だけ抜く方式も試したが、数値穴埋め・条文同定で精度が
    落ちた（実測: luna 0.960 -> 0.908）。条文は前後の文脈ごと読ませる方が良く、
    長い条文で末尾が切れる問題は上限を上げることで解消した
    （実測: 法第52条の答えが末尾にあり、2200字では num-hou-52-31 が「不明」）。
    """
    parts, used = [], 0
    for a in hits:
        chunk = f"【{a['law']}第{a['article']}条 {a['caption']}】" + a["text"]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - used)]
        if not chunk:
            break
        parts.append(chunk)
        used += len(chunk)
        if used >= max_chars:
            break
    return "\n\n".join(parts)


# ---------------------------------------------------------------- API

def chat(model: str, messages: list[dict], max_tokens: int = 2500,
         temperature: float = 0.0, retries: int = 4, tools: list | None = None) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
    if os.environ.get("NO_THINK"):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{BASE}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                j = json.load(r)
            dt = time.time() - t0
            msg = j["choices"][0]["message"]
            return {
                "msg": msg,
                "text": msg.get("content") or msg.get("reasoning") or "",
                "usage": j.get("usage", {}),
                "latency_s": dt,
                "finish": j["choices"][0].get("finish_reason"),
                "retries": attempt,
            }
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last) from None
        except Exception as e:  # タイムアウト・接続断
            last = f"{type(e).__name__}: {e}"
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last) from None
    raise RuntimeError(last or "unknown")


# ---------------------------------------------------------------- 実行

def build_context(task: dict, retriever: Retriever, articles: list[dict]) -> str:
    """RAG のコンテキストを組み立てる。run_one と selftest で同じ経路を使う。"""
    direct = resolve_refs(task["question"], articles)
    contained = retriever.search_containment(quoted_passage(task["question"]))
    hits = retriever.search(task["question"], k=3)
    seen = {(a["law"], a["article"]) for a in direct}
    merged = direct + [h for h in contained + hits if (h["law"], h["article"]) not in seen]
    return rag_context(merged)


def run_one(task: dict, model: str, retriever: Retriever | None, pricing: dict,
            articles: list[dict] | None = None, use_tools: bool = False) -> dict:
    system = SYSTEM_RAG if retriever else SYSTEM
    user = task["question"]
    if retriever:
        user = f"# 参考条文\n{build_context(task, retriever, articles or [])}\n\n# 質問\n{task['question']}"

    rec = {"id": task["id"], "kind": task["kind"], "model": model}
    try:
        # 推論モデルは思考でトークンを使い切る。実測: gpt-5.5 は max_tokens=400 で
        # calc_daylight 40件中39件が finish=length の空回答になった。RAG で条文が
        # 長いと思考も伸びるため、余裕を持たせる（実測: luna は 2500 で false_claim
        # 2件が空回答）。
        msgs = [{"role": "system", "content": system + (
            "\n\n計算ツールが使える場合は、手計算せずツールの結果を使って答えよ。" if use_tools else "")},
                {"role": "user", "content": user}]
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        t0 = time.time()
        n_calls = 0
        for _turn in range(8):
            r = chat(model, msgs, max_tokens=12000, tools=TOOLS if use_tools else None)
            for k in usage:
                usage[k] += (r["usage"] or {}).get(k, 0)
            tcs = r["msg"].get("tool_calls") or []
            if not tcs:
                break
            msgs.append({"role": "assistant", "content": r["msg"].get("content") or "", "tool_calls": tcs})
            for tc in tcs:
                n_calls += 1
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                msgs.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": call_tool(tc["function"]["name"], args)})
        r["usage"] = usage
        r["latency_s"] = time.time() - t0
        rec["tool_calls"] = n_calls
    except Exception as e:
        rec.update({"correct": False, "error": str(e)[:300], "latency_s": None})
        return rec

    ok, why = grade(task, r["text"])
    u = r["usage"] or {}
    pin = u.get("prompt_tokens", 0)
    pout = u.get("completion_tokens", 0)
    p = pricing.get(model, {})
    cost = (pin * p.get("in", 0) + pout * p.get("out", 0)) / 1_000_000 if p else None

    rec.update({
        "correct": ok,
        "why": why,
        "pred": r["text"][:600],
        "answer": task["answer"],
        "latency_s": round(r["latency_s"], 3),
        "prompt_tokens": pin,
        "completion_tokens": pout,
        "cost_usd": cost,
        "finish": r["finish"],
    })
    return rec


def summarize(rows: list[dict], model: str) -> dict:
    ok = [r for r in rows if r.get("correct")]
    err = [r for r in rows if r.get("error")]
    lat = [r["latency_s"] for r in rows if r.get("latency_s")]
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    by_kind: dict[str, dict] = {}
    for r in rows:
        k = r["kind"]
        d = by_kind.setdefault(k, {"n": 0, "ok": 0})
        d["n"] += 1
        d["ok"] += 1 if r.get("correct") else 0
    pts = [r["prompt_tokens"] for r in rows if r.get("prompt_tokens") is not None]
    return {
        "model": model,
        "n": len(rows),
        # エラー（上流502等）は不正解として数える。accuracy は answered でも併記する。
        "accuracy": round(len(ok) / len(rows), 4) if rows else 0,
        "accuracy_answered": round(len(ok) / max(1, len(rows) - len(err)), 4),
        "errors": len(err),
        "latency_median_s": round(statistics.median(lat), 3) if lat else None,
        "latency_p90_s": round(sorted(lat)[int(len(lat) * 0.9)], 3) if lat else None,
        "cost_total_usd": round(sum(costs), 6) if costs else None,
        "cost_per_task_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "prompt_tokens_median": int(statistics.median(pts)) if pts else None,
        "by_kind": {k: {"n": v["n"], "accuracy": round(v["ok"] / v["n"], 3)}
                    for k, v in sorted(by_kind.items())},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="カンマ区切りのモデルID")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--kind", default="", help="特定のkindだけ実行")
    ap.add_argument("--rag", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tasks", default=TASKS, help="問題ファイル（既定: data/tasks.jsonl）")
    ap.add_argument("--tools", action="store_true", help="天空率などの計算ツールを渡す")
    a = ap.parse_args()

    if not KEY:
        print("TEAI_API_KEY が未設定です", file=sys.stderr)
        return 2

    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8")]
    if a.kind:
        tasks = [t for t in tasks if t["kind"] == a.kind]
    if a.limit:
        import random
        rng = random.Random(a.seed)
        tasks = rng.sample(tasks, min(a.limit, len(tasks)))

    pricing = load_pricing()
    articles = load_articles()
    retriever = Retriever(articles) if a.rag else None
    tag = a.tag or ("rag" if a.rag else "base")
    outdir = os.path.join(ROOT, "results", tag)
    os.makedirs(outdir, exist_ok=True)

    summaries = []
    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        t0 = time.time()
        rows: list[dict] = []
        path = os.path.join(outdir, re.sub(r"[^A-Za-z0-9._-]", "_", model) + ".jsonl")
        with ThreadPoolExecutor(max_workers=a.concurrency) as ex, open(path, "w") as f:
            futs = {ex.submit(run_one, t, model, retriever, pricing, articles, a.tools): t for t in tasks}
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                rows.append(r)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                if i % 20 == 0:
                    acc = sum(1 for x in rows if x.get("correct")) / len(rows)
                    print(f"[{model}] {i}/{len(tasks)} acc={acc:.3f}", flush=True)
        s = summarize(rows, model)
        s["wall_s"] = round(time.time() - t0, 1)
        s["rag"] = bool(a.rag)
        s["tools"] = bool(a.tools)
        summaries.append(s)
        print(json.dumps(s, ensure_ascii=False, indent=1), flush=True)

    # 同じタグに後からモデルを足すことが多いので、既存サマリーとマージする
    # （実測: 上書きで前回のモデルの行が消え、比較表が欠けた）。
    spath = os.path.join(outdir, "summary.json")
    merged = {}
    if os.path.exists(spath):
        for x in json.load(open(spath)):
            merged[x["model"]] = x
    for x in summaries:
        merged[x["model"]] = x
    json.dump(list(merged.values()), open(spath, "w"), ensure_ascii=False, indent=1)
    print(f"\n-> {spath}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
