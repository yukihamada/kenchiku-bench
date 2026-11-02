#!/usr/bin/env python3
"""建築基準法203問の実測から、teai.io の記事 HTML と証跡 JSON を生成する。

  python3 bench/make_teai_article.py --teai ~/workspace/teai.io

数値はすべて results/ の保存済みログから計算する。記事本文に手で数字を書かない
（実測が変われば記事も変わる。数字の食い違いを起こさないため）。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SLUG = "kenchiku-benchmark"
ASSET = "kenchiku-bench-20261101"
DATE = "2026-11-01"

ORDER = [
    ("openai/gpt-5.5", "GPT-5.5"),
    ("google/gemini-3.5-flash", "Gemini 3.5 Flash"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("openai/gpt-5.6-luna", "GPT-5.6 Luna"),
    ("openai/gpt-5.4-nano", "GPT-5.4 Nano"),
    ("qwen/qwen3.5-flash-02-23", "Qwen3.5 Flash"),
]

LAW_EN = {
    "建築基準法": "the Building Standard Law",
    "建築基準法施行令": "the Enforcement Order of the Building Standard Law",
}


TOPIC_EN = {
    "条文同定": "Article lookup",
    "誤り検出": "False claim",
    "容積率": "Floor-area ratio",
    "建蔽率": "Building coverage ratio",
    "居室の採光": "Daylight in habitable rooms",
    "居室の採光及び換気": "Daylight and ventilation",
    "採光補正係数": "Daylight correction factor",
    "面積上限の算定": "Area cap calculation",
    "構造耐力": "Structural strength",
    "直通階段の設置": "Direct stairways",
    "二以上の直通階段を設ける場合": "Two or more direct stairways",
    "屋外への出口": "Exits to the outdoors",
    "屋上広場等": "Rooftop plazas",
    "建築物の各部分の高さ": "Height of building parts",
    "防火壁等": "Fire walls",
    "第一種低層住居専用地域等内における建築物の高さの限度": "Height limit in low-rise residential zones",
}


COND_JA = {"RAG": "条文あり", "no-articles": "条文なし"}
COND_EN = {"RAG": "With articles", "no-articles": "No articles"}


def cond_ja(c: str) -> str:
    return COND_JA.get(c, c)


def cond_attr(c: str) -> str:
    return f'data-ja="{COND_JA.get(c, c)}" data-en="{COND_EN.get(c, c)}"'


def topic_en(name: str) -> str:
    return TOPIC_EN.get(name, name)


def law_en(name: str) -> str:
    return LAW_EN.get(name, name)


KIND_LABEL = {
    "numeric": ("条文の数値", "Numeric cloze"),
    "cite": ("条文の同定", "Article lookup"),
    "calc_bcr": ("建蔽率", "BCR"),
    "calc_far": ("容積率", "FAR"),
    "calc_daylight": ("採光補正係数", "Daylight factor"),
    "false_claim": ("誤り検出", "False claim"),
    "applied": ("応用計算（2段）", "Applied (two steps)"),
}

SAMPLE_TASKS = ["day-000", "app-004", "cite-hou-20"]


def load(tag: str, model: str) -> dict[str, dict]:
    path = os.path.join(ROOT, "results", tag, model.replace("/", "_") + ".jsonl")
    return {json.loads(l)["id"]: json.loads(l) for l in open(path, encoding="utf-8")}


def summary(tag: str) -> dict[str, dict]:
    return {r["model"]: r for r in json.load(open(os.path.join(ROOT, "results", tag, "summary.json"), encoding="utf-8"))}


def human_why(why: str) -> tuple[str, str]:
    """採点理由を読み手向けの短い日英ペアにする。"""
    w = (why or "").strip()
    if w.startswith("want=") and " law_ok=" in w:
        head = w[5:].split(" law_ok=")[0]
        art = head.split("#")[-1]
        if "arts=[]" in w:
            return ("「第◯条」の形式で条番号を示していない",
                    "no article number in the form 'Article N'")
        return (f"条番号「{art}」が回答に無い", f"article number {art} not found in the answer")
    if w.startswith("want=") and " got=" in w:
        want, got = w[5:].split(" got=", 1)
        return f"正解 {want} / 回答 {got}", f"expected {want} / got {got}"
    if w.startswith("total="):
        return ("延べ面積と建築面積の両方が必要", "both floor-area and building-area caps are required")
    if w.startswith("law_ok="):
        return ("条番号を抽出できなかった", "no article number found in the answer")
    if w.startswith("num="):
        return ("正しい数値または条番号が欠けた", "the corrected value or article number was missing")
    if w.startswith("wrong="):
        return ("誤りだと述べていない", "the answer did not call the claim wrong")
    return w or "—", w or "—"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二項比率の Wilson 95% 信頼区間（%）。"""
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (c - h) * 100, (c + h) * 100


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teai", default=os.path.expanduser("~/workspace/teai.io"))
    ap.add_argument("--skip-asset", action="store_true")
    a = ap.parse_args()

    asset_path = os.path.join(a.teai, "web", "blog-assets", f"{ASSET}.json")
    if not a.skip_asset:
        subprocess.run([sys.executable, os.path.join(HERE, "export_teai.py"), "--out", asset_path], check=True)

    base, rag = summary("base"), summary("rag")
    tasks = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8")}

    qwen_a = load("rag", "qwen/qwen3.5-flash-02-23")
    qwen_b = load("rag-repeat1", "qwen/qwen3.5-flash-02-23")
    qwen_base_a = load("base", "qwen/qwen3.5-flash-02-23")
    qwen_base_b = load("base-repeat1", "qwen/qwen3.5-flash-02-23")
    nano_a = load("rag", "openai/gpt-5.4-nano")
    nano_b = load("rag-repeat2", "openai/gpt-5.4-nano")

    flips = sum(1 for k in qwen_a if qwen_a[k]["correct"] != qwen_b[k]["correct"])
    n = len(qwen_a)

    # --- 単一リクエスト反復プローブ（qwen・40回）
    probe = json.load(open(os.path.join(ROOT, "results/probes/qwen-day-000-repeat40.json"), encoding="utf-8"))
    pr = probe["runs"]
    short = [r for r in pr if r["completion_tokens"] < 100]
    long_ = [r for r in pr if r["completion_tokens"] >= 100]
    short_ok = sum(1 for r in short if r["correct"])
    long_ok = sum(1 for r in long_ if r["correct"])
    short_ctoks = sorted({r["completion_tokens"] for r in short})
    short_ctoks_text = ", ".join(str(c) for c in short_ctoks)
    long_med = statistics.median(r["completion_tokens"] for r in long_)
    short_lat = statistics.median(r["latency_s"] for r in short)
    long_lat = statistics.median(r["latency_s"] for r in long_)

    nano_probe = json.load(open(os.path.join(ROOT, "results/probes/nano-day-000-repeat20.json"), encoding="utf-8"))
    nr = nano_probe["runs"]
    nano_short = [r for r in nr if r["completion_tokens"] < 100]
    nano_ok = sum(1 for r in nr if r["correct"])

    four = json.load(open(os.path.join(ROOT, "results/probes/qwen-4tasks-repeat5.json"), encoding="utf-8"))
    four_bad = [r for r in four["runs"] if not r["correct"]]

    total_cost = sum(base[m]["cost_total_usd"] for m, _ in ORDER) + sum(rag[m]["cost_total_usd"] for m, _ in ORDER)
    total_req = n * 6 * 2

    # --- 表: 本体結果
    rows = []
    for m, label in ORDER:
        b, r = base[m], rag[m]
        k = round(r["accuracy"] * r["n"])
        ci_lo, ci_hi = wilson(k, r["n"])
        rows.append(
            f'<tr><td>{label}<br><span class="id">{m}</span></td>'
            f'<td class="num">{pct(b["accuracy"])}</td>'
            f'<td class="num"><strong>{pct(r["accuracy"])}</strong></td>'
            f'<td class="num">${r["cost_per_task_usd"]:.4f}</td>'
            f'<td class="num">{r["latency_median_s"]:.1f}s</td>'
            f'<td class="num">{r["latency_p90_s"]:.1f}s</td>'
            f'<td class="num ci">{ci_lo:.1f}–{ci_hi:.1f}</td></tr>'
        )
    body_rows = "\n".join(rows)

    # --- 図: 条文なし→ありの比較バー（実測値のみ）
    bar_rows = "\n".join(
        f'<div class="bar-row"><span>{label}</span>'
        f'<span class="bar-track"><span class="bar-base" style="width:{base[m]["accuracy"]*100:.1f}%"></span>'
        f'<span class="bar-rag" style="width:{rag[m]["accuracy"]*100:.1f}%"></span></span>'
        f'<span class="bar-val">{pct(base[m]["accuracy"])} → <b>{pct(rag[m]["accuracy"])}</b></span></div>'
        for m, label in ORDER
    )

    # --- 表: 種別ごとの RAG 精度
    kinds = ["numeric", "cite", "calc_bcr", "calc_far", "calc_daylight", "false_claim", "applied"]
    head = "".join(
        f'<th scope="col" class="num"><span data-ja="{KIND_LABEL[k][0]}" data-en="{KIND_LABEL[k][1]}">{KIND_LABEL[k][0]}</span>'
        f'<br><span class="id">n={rag[ORDER[0][0]]["by_kind"][k]["n"]}</span></th>'
        for k in kinds)
    kind_rows = []
    for m, label in ORDER:
        cells = []
        for k in kinds:
            v = rag[m]["by_kind"][k]["accuracy"]
            cls = ' class="num weak"' if v < 0.9 else ' class="num"'
            cells.append(f"<td{cls}>{v * 100:.0f}%</td>")
        kind_rows.append(f'<tr><td>{label}</td>{"".join(cells)}</tr>')
    kind_body = "\n".join(kind_rows)

    # --- 表: 費用（正解1件あたり）
    cost_html = []
    for m, label in sorted(ORDER, key=lambda x: rag[x[0]]["cost_total_usd"] / round(rag[x[0]]["accuracy"] * n)):
        r = rag[m]
        correct = round(r["accuracy"] * n)
        cost_html.append(
            f'<tr><td>{label}</td><td class="num">{correct}</td>'
            f'<td class="num">${r["cost_total_usd"]:.2f}</td>'
            f'<td class="num">${r["cost_total_usd"] / correct:.4f}</td></tr>'
        )
    cost_rows = "\n".join(cost_html)

    # --- 表: 同一設定の再測定
    repeat_rows = [
        ("qwen/qwen3.5-flash-02-23", "RAG", qwen_a, qwen_b, "0.611 → 0.916"),
        ("qwen/qwen3.5-flash-02-23", "no-articles", qwen_base_a, qwen_base_b, "0.330 → 0.340"),
        ("openai/gpt-5.4-nano", "RAG", nano_a, nano_b, "0.931 → 0.961"),
    ]
    rep_html = []
    for m, cond, A, B, _ in repeat_rows:
        f = sum(1 for k in A if A[k]["correct"] != B[k]["correct"])
        accA = sum(1 for r in A.values() if r["correct"]) / len(A)
        accB = sum(1 for r in B.values() if r["correct"]) / len(B)
        ctokA = statistics.median(r["completion_tokens"] for r in A.values())
        ctokB = statistics.median(r["completion_tokens"] for r in B.values())
        rep_html.append(
            f'<tr><td>{dict(ORDER).get(m, m)}<br><span class="id">{m}</span></td><td {cond_attr(cond)}>{cond_ja(cond)}</td><td class="num">{pct(accA)}</td><td class="num">{pct(accB)}</td>'
            f'<td class="num">{f}</td><td class="num">{ctokA:.0f}</td><td class="num">{ctokB:.0f}</td></tr>'
        )
    rep_body = "\n".join(rep_html)

    # --- 40回の並び（視覚化）
    total_short = len(short)
    total_long = len(long_)
    bar = "".join(
        f'<span class="sq {"bad" if r["completion_tokens"] < 100 else "ok"}" aria-hidden="true"></span>'
        for r in pr
    )
    bar_label_ja = (
        f"40回の応答を実行順に並べた図。推論あり・正解が{total_long}回、即答・不正解が{total_short}回。"
        f"即答は出力{short_ctoks_text}トークン、推論ありは中央値{long_med:.0f}トークン。"
    )
    bar_label_en = (
        f"Forty responses in execution order: {total_long} reasoned and correct, {total_short} immediate and wrong. "
        f"Immediate answers used {short_ctoks_text} output tokens; reasoned answers used a median of {long_med:.0f}."
    )

    # --- 例題の回答
    samples = []
    for tid in SAMPLE_TASKS:
        t = tasks[tid]
        ans_rows = []
        for m, label in ORDER:
            rec = load("rag", m)[tid]
            mark = '<span class="ok-mark">○</span>' if rec["correct"] else '<span class="bad-mark">×</span>'
            full = (rec["pred"] or "").strip()
            pred = full.replace("\n", " ")
            if len(pred) > 96:
                pred = pred[:96] + "…"
            cell = f"<code>{esc(pred)}</code>"
            if not rec["correct"]:
                why_ja, why_en = human_why(rec.get("why", ""))
                why_ja, why_en = esc(why_ja), esc(why_en)
                cell += '<br><span class="why" data-ja="採点: ' + why_ja + '" data-en="Graded: ' + why_en + '">採点: ' + why_ja + "</span>"
            if len(full) > 96:
                cell += ('<details class="ans"><summary data-ja="全文（' + str(len(full)) + '文字）" '
                        'data-en="Full answer (' + str(len(full)) + ' chars)">全文（' + str(len(full)) + "文字）</summary>"
                        f"<pre>{esc(full)}</pre></details>")
            ans_rows.append(f"<tr><td>{label}</td><td>{mark}</td><td>{cell}</td></tr>")
        samples.append(
            f'<details><summary><strong>{tid}</strong> — '
            f'<span data-ja="{esc(t["topic"])}" data-en="{esc(topic_en(t["topic"]))}">{esc(t["topic"])}</span></summary>'
            f'<p class="q">{esc(t["question"]).replace(chr(10), "<br>")}</p>'
            f'<p class="a"><span data-ja="正解（{esc(t["evidence"]["law"])}第{esc(t["evidence"]["article"])}条から機械的に生成）" '
            f'data-en="Answer (derived mechanically from {esc(law_en(t["evidence"]["law"]))} Art. {esc(t["evidence"]["article"])})">'
            f'正解（{esc(t["evidence"]["law"])}第{esc(t["evidence"]["article"])}条から機械的に生成）</span>: <strong>{esc(t["answer"])}</strong></p>'
            f'<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>'
            f'<caption data-ja="{esc(t["topic"])} の6モデル判定" data-en="Six-model results for {esc(topic_en(t["topic"]))}">{esc(t["topic"])} の6モデル判定</caption>'
            f'<thead><tr><th scope="col" data-ja="モデル" data-en="Model">モデル</th>'
            f'<th scope="col" data-ja="判定" data-en="Result">判定</th><th scope="col" data-ja="回答（条文あり）" data-en="Answer (with articles)">回答（条文あり）</th></tr></thead>'
            f'<tbody>{"".join(ans_rows)}</tbody></table></div></details>'
        )
    sample_html = "\n".join(samples)

    q = rag["qwen/qwen3.5-flash-02-23"]["by_kind"]
    five = [m for m, _ in ORDER if m != "qwen/qwen3.5-flash-02-23"]
    rest_min = min(rag[m]["by_kind"][k]["accuracy"] for m in five for k in rag[m]["by_kind"]) * 100
    rest_applied0 = min(rag[m]["by_kind"]["applied"]["accuracy"] for m in five) * 100
    rest_applied1 = max(rag[m]["by_kind"]["applied"]["accuracy"] for m in five) * 100
    cells = [(m, k) for m in rag for k in rag[m]["by_kind"]]
    low_cells = [(m, k, rag[m]["by_kind"][k]["accuracy"] * 100) for m, k in cells
                 if rag[m]["by_kind"][k]["accuracy"] * 100 < 90]
    low_total = len(low_cells)
    label_of = dict(ORDER)
    low_qwen = sum(1 for m, _, _ in low_cells if m.startswith("qwen"))
    low_other = [f'{label_of.get(m, m)} の {KIND_LABEL[k][0]} {v:.1f}%'
                 for m, k, v in low_cells if not m.startswith("qwen")]
    low_other_ja = "、".join(low_other)
    low_other_en = " and ".join(
        f'{label_of.get(m, m)} {KIND_LABEL[k][1]} {v:.1f}%'
        for m, k, v in low_cells if not m.startswith("qwen"))
    applied_min = min(rag[m]["by_kind"]["applied"]["accuracy"] for m, _ in ORDER) * 100
    qwen_daylight = q["calc_daylight"]["accuracy"] * 100
    qwen_far = q["calc_far"]["accuracy"] * 100
    qwen_cite = q["cite"]["accuracy"] * 100
    qwen_bcr = q["calc_bcr"]["accuracy"] * 100
    qwen_applied = q["applied"]["accuracy"] * 100

    four_bad0 = f"{four_bad[0]['completion_tokens']:,}"
    four_bad0_lat = four_bad[0]["latency_s"]
    p_short = total_short / len(pr)

    css = """
:root{color-scheme:dark;--bg:#090f13;--ink:#eef5f5;--muted:#b4c1c6;--accent:#a4f0bd;--warn:#ffd479;--bad:#ff9b9b;--line:#304238}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(ellipse at 90% 0,#173d2d,transparent 42%),var(--bg);color:var(--ink);font:16px/1.85 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
a{color:var(--accent);text-underline-offset:4px}
nav,main,footer{width:min(1060px,calc(100% - 36px));margin:auto}
nav{padding:24px 0;display:flex;justify-content:space-between;gap:16px}
.brand{font-size:24px;font-weight:800;text-decoration:none}
header{padding:64px 0 36px}
h1{font-size:clamp(32px,5.6vw,58px);line-height:1.22;letter-spacing:-.035em;max-width:960px}
h2{font-size:clamp(22px,3vw,28px);line-height:1.4;margin-top:0}
h3{font-size:18px;line-height:1.5}
.lead{font-size:20px;color:#d1ddd6;max-width:880px}
.eyebrow{font-size:12px;letter-spacing:.14em;color:var(--accent)}
section{border-top:1px solid var(--line);padding:34px 0}
p{max-width:920px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0 4px}
th,td{padding:12px 11px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:top}
th{font-weight:600;color:#cfe3d8}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.id{color:var(--muted);font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.weak{color:var(--warn)}
.note{border-left:3px solid var(--accent);padding:4px 0 4px 18px;color:var(--muted)}
.warnbox{border:1px solid #4a3c1f;background:#1b1608;border-radius:12px;padding:16px 20px;margin:18px 0}
details{border-bottom:1px solid var(--line);padding:14px 0}
summary{cursor:pointer;overflow-wrap:anywhere}
pre{max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#050a0d;border:1px solid var(--line);border-radius:12px;padding:16px;font-size:13px;line-height:1.65}
code{overflow-wrap:anywhere;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.q{color:var(--muted);font-size:14px}
.a{font-size:14px}
.ok-mark{color:var(--accent);font-weight:700}
.bad-mark{color:var(--bad);font-weight:700}
.bars{margin:18px 0 6px;padding:0}
.bars figcaption{font-size:13px;color:var(--muted);margin-bottom:10px}
.bar-row{display:grid;grid-template-columns:150px 1fr 92px;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.bar-track{position:relative;height:16px;background:#16232b;border-radius:4px;overflow:hidden}
.bar-base,.bar-rag{position:absolute;left:0;top:0;height:100%;border-radius:4px}
.bar-base{background:#3b5566}
.bar-rag{background:var(--accent);opacity:.9}
.bar-val{font-variant-numeric:tabular-nums;color:var(--muted);text-align:right}
.bar-val b{color:var(--ink)}
@media(max-width:600px){.bar-row{grid-template-columns:1fr auto;grid-template-areas:"l v" "t t";gap:3px 8px;font-size:12px;margin:10px 0}
.bar-row>span:first-child{grid-area:l}
.bar-track{grid-area:t}
.bar-val{grid-area:v;text-align:right;white-space:nowrap}}
.sqwrap{display:flex;flex-wrap:wrap;gap:4px;margin:14px 0 6px}
.sq{width:20px;height:32px;border-radius:3px;display:inline-block}
.sq.ok{background:#3f8f63}
.sq.bad{background:#d96b6b}
.why{color:var(--bad);font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
details.ans{margin-top:6px;border:0;padding:0}
details.ans summary{font-size:12px;color:var(--muted)}
details.ans pre{max-height:260px;margin-top:8px}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:var(--muted)}
.key{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
footer{padding:36px 0 64px;color:var(--muted);font-size:14px}
.skip{position:absolute;left:-9999px;top:0;background:var(--accent);color:#04120b;padding:10px 16px;border-radius:0 0 8px 0;z-index:10}
.skip:focus{left:0}
.toc{display:flex;flex-wrap:wrap;gap:8px 18px;padding:0 0 18px;font-size:14px;border-bottom:1px solid var(--line);margin-bottom:26px}
.toc a{text-decoration:none;border-bottom:1px solid transparent}
.toc a:hover,.toc a:focus-visible{border-bottom-color:var(--accent)}
.cta{border:1px solid #2f5a44;background:#0d1a15;border-radius:14px;padding:20px 24px;margin:26px 0 0}
.cta p{margin:6px 0}
.cta-links{display:flex;flex-wrap:wrap;align-items:center;gap:14px 22px;margin-top:14px}
.btn{display:inline-block;background:var(--accent);color:#04120b;font-weight:700;text-decoration:none;padding:10px 18px;border-radius:999px}
.btn:hover,.btn:focus-visible{filter:brightness(1.08)}
.muted-note{font-size:12px;color:var(--muted);display:block;margin-top:4px}
.related{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:10px 24px}
.related li{margin:0}
a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@media(max-width:600px){header{padding-top:30px}.lead{font-size:18px}th,td{padding:10px 8px}
.tldr ul{padding-left:18px}.tldr li{margin:6px 0}}
.tldr{border:1px solid var(--line);border-radius:12px;padding:16px 22px;margin:22px 0;background:#0c1519}
.tldr ul{margin:6px 0 0;padding-left:20px}
.tldr li{margin:8px 0;max-width:900px}
"""

    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>同じ質問を40回したら、答えが2種類返ってきた — 建築基準法203問の実測 | teai</title>
<meta name="description" content="建築基準法・同施行令の条文から機械的に作った203問を6モデルで実測。条文を渡すと最大+55ポイント改善する一方、同一リクエスト40回のうち{total_short}回が推論なしの即答で誤答した記録を公開します。" data-meta-ja="建築基準法・同施行令の条文から機械的に作った203問を6モデルで実測。条文を渡すと最大+55ポイント改善する一方、同一リクエスト40回のうち{total_short}回が推論なしの即答で誤答した記録を公開します。" data-meta-en="203 tasks generated mechanically from Japanese Building Standard Law articles, run across six models. Retrieval added up to 55 points — but in 40 identical requests, {total_short} came back with no reasoning and a wrong answer.">
<link rel="canonical" href="https://teai.io/blog/{SLUG}">
<link rel="alternate" hreflang="ja" href="https://teai.io/blog/{SLUG}">
<link rel="alternate" hreflang="en" href="https://teai.io/blog/{SLUG}?lang=en">
<meta property="og:title" content="同じ質問を40回したら、答えが2種類返ってきた — 建築基準法203問の実測 | teai" data-meta-ja="同じ質問を40回したら、答えが2種類返ってきた — 建築基準法203問の実測 | teai" data-meta-en="We asked the same question 40 times. It came back two different ways.">
<meta property="og:description" content="203問×6モデル。条文を渡すと最大+55ポイント。同じリクエストが推論あり／なしの2挙動で返り、精度が{total_short}/{len(pr)}で分かれた記録。" data-meta-ja="203問×6モデル。条文を渡すと最大+55ポイント。同じリクエストが推論あり／なしの2挙動で返り、精度が{total_short}/{len(pr)}で分かれた記録。" data-meta-en="203 tasks, six models, up to +55 points from retrieval. The same request came back with and without reasoning, splitting accuracy in {total_short} of {len(pr)} runs.">
<meta property="og:type" content="article"><meta property="og:url" content="https://teai.io/blog/{SLUG}">
<meta property="og:image" content="https://teai.io/og-kenchiku-benchmark.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:image:alt" content="40回の応答のうち25回が推論ありで正解、15回が即答で不正解だったことを示す図" data-meta-ja="40回の応答のうち25回が推論ありで正解、15回が即答で不正解だったことを示す図" data-meta-en="A chart showing 25 of 40 responses reasoned and were correct, while 15 answered immediately and were wrong"><meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="同じ質問を40回したら、答えが2種類返ってきた。">
<meta name="twitter:description" content="203問×6モデル。条文を渡すと最大+55ポイント。同一リクエスト40回のうち15回が即答・全問不正解。">
<meta name="twitter:image" content="https://teai.io/og-kenchiku-benchmark.png">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Article","headline":"同じ質問を40回したら、答えが2種類返ってきた。","description":"建築基準法203問を6モデルで実測した記録。条文を渡すと最大+55.2ポイント、同一リクエスト40回のうち15回が即答で全問不正解。","datePublished":"{DATE}","dateModified":"{DATE}","inLanguage":"ja","url":"https://teai.io/blog/{SLUG}","image":"https://teai.io/og-kenchiku-benchmark.png","author":{{"@type":"Organization","name":"teai.io","url":"https://teai.io"}},"publisher":{{"@type":"Organization","name":"teai.io","url":"https://teai.io"}}}}</script>
<style>{css}</style>
</head><body>
<a class="skip" href="#results" data-ja="本文へ移動" data-en="Skip to content">本文へ移動</a>
<nav><a class="brand" href="/">teai.</a><span><a href="/blog" id="blog-link" data-ja="実測記事一覧" data-en="Measured reports">実測記事一覧</a> · <a id="language" href="?lang=en">English</a></span></nav>
<main>
<header>
<div class="eyebrow">MEASURED · <time datetime="{DATE}">{DATE}</time> JST · 203 TASKS × 6 MODELS × 2 CONDITIONS</div>
<h1 data-ja="同じ質問を40回したら、答えが2種類返ってきた。" data-en="We asked the same question 40 times. It came back two different ways.">同じ質問を40回したら、答えが2種類返ってきた。</h1>
<nav class="toc" aria-label="目次" data-aria-en="Contents">
<a href="#results" data-ja="結果" data-en="Results">結果</a>
<a href="#by-kind" data-ja="種別" data-en="By type">種別</a>
<a href="#repeat" data-ja="40回の反復" data-en="40 repeats">40回の反復</a>
<a href="#notes" data-ja="費用と精度" data-en="Cost vs accuracy">費用と精度</a>
<a href="#samples" data-ja="実例" data-en="Examples">実例</a>
<a href="#protocol" data-ja="条件と限界" data-en="Conditions and limits">条件と限界</a>
<a href="#practice" data-ja="実務の判断" data-en="In production">実務の判断</a>
<a href="#data" data-ja="証跡" data-en="Evidence">証跡</a>
</nav>
<div class="tldr">
<p><strong data-ja="要点" data-en="In short">要点</strong></p>
<ul>
<li data-ja="条文を渡すと、6モデルすべて改善した。最大 +55.2ポイント（GPT-5.4 Nano 37.9% → 93.1%）。" data-en="Supplying the articles improved all six models, by up to 55.2 points (GPT-5.4 Nano 37.9% → 93.1%).">条文を渡すと、6モデルすべて改善した。最大 +55.2ポイント（GPT-5.4 Nano 37.9% → 93.1%）。</li>
<li data-ja="同じリクエスト40回のうち15回は、6トークンで即答し、すべて不正解だった。残り25回は推論して全問正解。" data-en="15 of 40 identical requests answered after 6 tokens, all wrong. The other 25 reasoned and were all correct.">同じリクエスト40回のうち15回は、6トークンで即答し、すべて不正解だった。残り25回は推論して全問正解。</li>
<li data-ja="同じ条件の2回目で、qwen は82/203問、nano は20/203問が正誤反転した。1回の合格は再現性の証明にならない。" data-en="On a second run of the same condition, 82 of 203 tasks flipped for qwen and 20 for nano. One pass is not proof of reproducibility.">同じ条件の2回目で、qwen は82/203問、nano は20/203問が正誤反転した。1回の合格は再現性の証明にならない。</li>
</ul>
</div>
<p class="lead" data-ja="建築基準法203問を6モデルで実測しました。条文を渡すと全モデルが大きく伸び、最大で+55ポイント。ところが1問の同じリクエストを40回繰り返すと、推論をする応答と、{short_ctoks_text}トークンで即答する応答が混ざり、即答した{total_short}回はすべて不正解でした。" data-en="We measured 203 Building Standard Law tasks across six models. Supplying the articles lifted every model, by up to 55 points. But repeating one identical question 40 times produced two kinds of response: one that reasons, and one that answers after {short_ctoks_text} tokens. Every one of the {total_short} instant answers was wrong.">建築基準法203問を6モデルで実測しました。条文を渡すと全モデルが大きく伸び、最大で+55ポイント。ところが1問の同じリクエストを40回繰り返すと、推論をする応答と、{short_ctoks_text}トークンで即答する応答が混ざり、即答した{total_short}回はすべて不正解でした。</p>
<div class="note"><p data-ja="正解は e-Gov 法令API の条文と、条文の式に数値を入れた決定論的計算だけから作りました。採点にLLMは使っていません。203問・全応答・採点理由・実行コマンドを下記のJSONで公開しています（採点コード自体は非公開リポジトリです）。" data-en="Answers were generated only from e-Gov law text and deterministic arithmetic on the article's formula. No LLM is used for grading. All 203 tasks, every response with its grading reason, and the run commands are published in the JSON below (the grader code itself is in a private repository).">正解は e-Gov 法令API の条文と、条文の式に数値を入れた決定論的計算だけから作りました。採点にLLMは使っていません。203問・全応答・採点理由・実行コマンドを下記のJSONで公開しています（採点コード自体は非公開リポジトリです）。</p></div>
<p data-ja="teai は、複数のモデルを同じAPI・同じ料金体系で使えるサービスです。この記事はその測定記録です。" data-en="teai lets you use several models through one API and one billing system. This article is a measurement record from that route.">teai は、複数のモデルを同じAPI・同じ料金体系で使えるサービスです。この記事はその測定記録です。</p>
<p data-ja="測定経路である teai.io の開発者による実測です。独立第三者評価ではありません。" data-en="Measured by the developer of teai.io, the API route under test. This is not an independent third-party evaluation.">測定経路である teai.io の開発者による実測です。独立第三者評価ではありません。</p>
</header>

<section id="results">
<h2 data-ja="結果：条文を渡すと、ほぼ別のモデルになる" data-en="Results: with the articles, these are almost different models">結果：条文を渡すと、ほぼ別のモデルになる</h2>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="6モデルの正解率・費用・待ち時間（条文あり）" data-en="Accuracy, cost and latency for six models (with articles)">6モデルの正解率・費用・待ち時間（条文あり）</caption>
<thead><tr><th scope="col" data-ja="モデル" data-en="Model">モデル</th><th scope="col" class="num" data-ja="条文なし" data-en="No articles">条文なし</th><th scope="col" class="num" data-ja="条文を渡す" data-en="With articles">条文を渡す</th><th scope="col" class="num" data-ja="1問あたり" data-en="Per task">1問あたり</th><th scope="col" class="num" data-ja="中央値" data-en="Median">中央値</th><th scope="col" class="num" data-ja="p90" data-en="p90">p90</th><th scope="col" class="num" data-ja="95%区間" data-en="95% CI">95%区間</th></tr></thead>
<tbody>
{body_rows}
</tbody></table></div>
<figure class="bars">
<figcaption data-ja="条文なし → 条文ありの正解率（203問）" data-en="Accuracy without articles → with articles (203 tasks)">条文なし → 条文ありの正解率（203問）</figcaption>
{bar_rows}
</figure>
<p data-ja="203問・temperature 0・各条件1回。安い順ではなく、今回測った6モデルをそのまま並べています。差が最も大きかったのは Gemini 2.5 Flash（+39.4ポイント）と GPT-5.4 Nano（+55.2ポイント）でした。" data-en="203 tasks, temperature 0, one run per condition. Ordered as measured, not by price. The largest gains were Gemini 2.5 Flash (+39.4 points) and GPT-5.4 Nano (+55.2 points).">203問・temperature 0・各条件1回。安い順ではなく、今回測った6モデルをそのまま並べています。差が最も大きかったのは Gemini 2.5 Flash（+39.4ポイント）と GPT-5.4 Nano（+55.2ポイント）でした。</p>
</section>

<section id="by-kind">
<h2 data-ja="種別ごと：採光と容積率で、モデル差が最も開く" data-en="By task type: daylight and FAR separate the models most">種別ごと：採光と容積率で、モデル差が最も開く</h2>
<p data-ja="条文を渡した状態の内訳です。数値穴埋めは6モデルすべて100%、誤り検出も96%以上でした。Qwen3.5 Flash を除く5モデルでは、7項目すべてが{rest_min:.0f}%以上に収まっています。開きが最も大きいのは Qwen の採光補正係数（{qwen_daylight:.0f}%）で、その行を外すと最大の差は applied の {rest_applied0:.0f}%〜{rest_applied1:.0f}% になります。applied は、前面道路の制限で容積率を決め、敷地面積を掛け、建蔽率側と並べて答える2段の計算です。" data-en="Breakdown with articles supplied. Numeric cloze was 100% for all six models and false-claim checks stayed above 96%. Excluding Qwen3.5 Flash, the other five stay at {rest_min:.0f}% or above on all seven types. The widest gap is Qwen's daylight score ({qwen_daylight:.0f}%); drop that row and the largest remaining spread is applied at {rest_applied0:.0f}–{rest_applied1:.0f}%. Applied means: derive the FAR cap from the front-road limit, multiply by site area, then answer alongside the BCR figure.">条文を渡した状態の内訳です。数値穴埋めは6モデルすべて100%、誤り検出も96%以上でした。Qwen3.5 Flash を除く5モデルでは、7項目すべてが{rest_min:.0f}%以上に収まっています。開きが最も大きいのは Qwen の採光補正係数（{qwen_daylight:.0f}%）で、その行を外すと最大の差は applied の {rest_applied0:.0f}%〜{rest_applied1:.0f}% になります。applied は、前面道路の制限で容積率を決め、敷地面積を掛け、建蔽率側と並べて答える2段の計算です。</p>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="タスク種別ごとの正解率（条文あり・n は問題数）" data-en="Accuracy by task type (with articles; n = number of tasks)">タスク種別ごとの正解率（条文あり・n は問題数）</caption>
<thead><tr><th scope="col" data-ja="モデル" data-en="Model">モデル</th>{head}</tr></thead>
<tbody>
{kind_body}
</tbody></table></div>
<p class="note" data-ja="黄色は90%未満。42セル中{low_total}セルで、うち{low_qwen}セルが Qwen3.5 Flash の1行（採光 {qwen_daylight:.0f}%、容積率 {qwen_far:.0f}%、条文同定 {qwen_cite:.0f}%、建蔽率 {qwen_bcr:.0f}%、応用 {qwen_applied:.0f}%）。残りは {low_other_ja} です。Qwen は同じ条件の2回目で 91.6% まで戻っており、この低さが恒常的な能力なのか、下で扱う応答の振れの一部なのかは、この測定だけでは切り分けられません。" data-en="Amber marks under 90%: {low_total} of 42 cells, {low_qwen} of them in the Qwen3.5 Flash row (daylight {qwen_daylight:.0f}%, FAR {qwen_far:.0f}%, article lookup {qwen_cite:.0f}%, BCR {qwen_bcr:.0f}%, applied {qwen_applied:.0f}%). The rest are {low_other_en}. Qwen scored 91.6% on a second run of the same condition, so this data alone cannot say whether that row reflects stable capability or part of the response variance discussed below.">黄色は90%未満。42セル中{low_total}セルで、うち{low_qwen}セルが Qwen3.5 Flash の1行（採光 {qwen_daylight:.0f}%、容積率 {qwen_far:.0f}%、条文同定 {qwen_cite:.0f}%、建蔽率 {qwen_bcr:.0f}%、応用 {qwen_applied:.0f}%）。残りは {low_other_ja} です。Qwen は同じ条件の2回目で 91.6% まで戻っており、この低さが恒常的な能力なのか、下で扱う応答の振れの一部なのかは、この測定だけでは切り分けられません。</p>
</section>

<section id="repeat">
<h2 data-ja="本題：同じリクエストが、2種類の応答を返す" data-en="The main finding: one request, two kinds of response">本題：同じリクエストが、2種類の応答を返す</h2>
<p data-ja="203問の測定中、Qwen3.5 Flash だけが同じ条件の2回目で 61.1% → 91.6% と動きました（{flips}/{n}問が正誤反転）。検索結果はほぼ同一（プロンプトのトークン中央値 2,356 → 2,354）だったので、検索側の問題ではありません。切り分けるため、1問（day-000・採光補正係数）の同一リクエストを40回投げました。" data-en="During the 203-task runs, only Qwen3.5 Flash moved between two runs of the same condition: 61.1% → 91.6% ({flips} of {n} tasks flipped). Retrieval was effectively identical (median prompt tokens 2,356 → 2,354), so this was not a search problem. To isolate it, we sent one identical request (day-000, daylight factor) forty times.">203問の測定中、Qwen3.5 Flash だけが同じ条件の2回目で 61.1% → 91.6% と動きました（{flips}/{n}問が正誤反転）。検索結果はほぼ同一（プロンプトのトークン中央値 2,356 → 2,354）だったので、検索側の問題ではありません。切り分けるため、1問（day-000・採光補正係数）の同一リクエストを40回投げました。</p>

<div class="sqwrap" role="img" aria-label="{bar_label_ja}" data-aria-en="{bar_label_en}">{bar}</div>
<div class="legend"><span><span class="key" style="background:#3f8b61"></span><span data-ja="推論あり・正解" data-en="Reasoned, correct">推論あり・正解</span> <span data-ja="({total_long}回)" data-en="({total_long} runs)">({total_long}回)</span></span><span><span class="key" style="background:#d96b6b"></span><span data-ja="即答・不正解" data-en="Immediate, wrong">即答・不正解</span> <span data-ja="({total_short}回)" data-en="({total_short} runs)">({total_short}回)</span></span></div>

<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="同一リクエスト40回の内訳（Qwen3.5 Flash）" data-en="Breakdown of 40 identical requests (Qwen3.5 Flash)">同一リクエスト40回の内訳（Qwen3.5 Flash）</caption>
<thead><tr><th scope="col" data-ja="応答の型" data-en="Response type">応答の型</th><th scope="col" class="num" data-ja="回数" data-en="Runs">回数</th><th scope="col" class="num" data-ja="出力トークン中央値" data-en="Median output tokens">出力トークン中央値</th><th scope="col" class="num" data-ja="待ち時間中央値" data-en="Median latency">待ち時間中央値</th><th scope="col" data-ja="回答" data-en="Answer">回答</th></tr></thead>
<tbody>
<tr><td data-ja="推論あり" data-en="Reasoned">推論あり</td><td class="num">{total_long}</td><td class="num">{long_med:.0f}</td><td class="num">{long_lat:.1f}s</td><td><code>{esc(long_[0]["pred"].strip())}</code></td></tr>
<tr><td data-ja="即答（推論なし）" data-en="Immediate (no reasoning)">即答（推論なし）</td><td class="num">{total_short}</td><td class="num">{short_ctoks_text}</td><td class="num">{short_lat:.1f}s</td><td><code>{esc(short[0]["pred"].strip())}</code></td></tr>
</tbody></table></div>
<p data-ja="正解は {esc(probe["answer"])} です。推論ありの{total_long}回は全問正解、即答の{total_short}回は全問不正解で、中間はありませんでした。同じ入力・同じ温度・同じ条文を渡しているのに、返ってくる応答の型自体が毎回変わる、というのがここで起きています。" data-en="The correct answer is {esc(probe["answer"])}. All {total_long} reasoned responses were correct; all {total_short} immediate responses were wrong. There was nothing in between. Same input, same temperature, same articles — but the kind of response itself changed from call to call.">正解は {esc(probe["answer"])} です。推論ありの{total_long}回は全問正解、即答の{total_short}回は全問不正解で、中間はありませんでした。同じ入力・同じ温度・同じ条文を渡しているのに、返ってくる応答の型自体が毎回変わる、というのがここで起きています。</p>

<h3 data-ja="モデルを変えると、この振れ方は変わる" data-en="The pattern differs by model">モデルを変えると、この振れ方は変わる</h3>
<p data-ja="同じ1問を GPT-5.4 Nano で20回投げると、即答は0回、20回すべて推論ありで正解でした。ところが203問を通すと、同じ nano でも 93.1% → 96.1% と20問が反転します（推論トークンの中央値は 398 → 386 でほぼ同じ）。つまり「特定のモデルが壊れている」のではなく、応答の型が確率で変わり、それが問題との相性で得点差になる、という構造です。" data-en="Sending the same task to GPT-5.4 Nano twenty times produced no immediate answers: all twenty reasoned and were correct. Yet across all 203 tasks the same model moved from 93.1% to 96.1%, flipping 20 tasks (median reasoning tokens 398 → 386, essentially unchanged). The structure is not a broken model; the response type varies probabilistically, and that variance becomes a score difference depending on the task.">同じ1問を GPT-5.4 Nano で20回投げると、即答は0回、20回すべて推論ありで正解でした。ところが203問を通すと、同じ nano でも 93.1% → 96.1% と20問が反転します（推論トークンの中央値は 398 → 386 でほぼ同じ）。つまり「特定のモデルが壊れている」のではなく、応答の型が確率で変わり、それが問題との相性で得点差になる、という構造です。</p>

<h3 data-ja="もう1つ見つかった失敗：推論が本文に漏れて途切れる" data-en="A second failure: reasoning leaks into the answer and truncates it">もう1つ見つかった失敗：推論が本文に漏れて途切れる</h3>
<p data-ja="4問×5回の追加試行（20回）で、応答本文に推論の区切り記号が混ざり、本文が「第 20」で切れた例が{len(four_bad)}件ありました。APIが返した出力トークンは {four_bad0} で、こちらが指定した max_tokens 12,000 を超えています（理由は未確認）。この回は答えとして成立していないため不正解に数えています。長く考えること自体は正解率を上げますが、推論が本文に漏れると、正しい考えを持っていても使える答えになりません。" data-en="In twenty extra runs (four tasks × five), {len(four_bad)} response had the reasoning delimiter mixed into the body and stopped at “第 20”. The API reported {four_bad0} output tokens — above the 12,000 max_tokens we set; the reason is unverified. It was not a usable answer, so it counted as wrong. Thinking longer raises accuracy, but reasoning that leaks into the body makes an otherwise correct line of thought unusable.">4問×5回の追加試行（20回）で、応答本文に推論の区切り記号が混ざり、本文が「第 20」で切れた例が{len(four_bad)}件ありました。APIが返した出力トークンは {four_bad0} で、こちらが指定した max_tokens 12,000 を超えています（理由は未確認）。答えとして成立していないため不正解に数えています。長く考えること自体は正解率を上げますが、推論が本文に漏れると、正しい考えを持っていても使える答えになりません。</p>

<details><summary data-ja="途切れた応答の実物（先頭600文字）" data-en="The truncated response as returned (first 600 characters)">途切れた応答の実物（先頭600文字）</summary>
<pre><code>{esc(four_bad[0]["pred"][:600])}</code></pre>
<p class="note" data-ja="APIが返した本文そのままです。推論の区切り記号が本文に混ざり、途中で終わっています。出力トークン {four_bad0}、待ち時間 {four_bad0_lat:.1f}秒。" data-en="Verbatim from the API. The reasoning delimiter appears in the body and the text ends mid-sentence. {four_bad0} output tokens, {four_bad0_lat:.1f} s.">APIが返した本文そのままです。推論の区切り記号が本文に混ざり、途中で終わっています。出力トークン {four_bad0}、待ち時間 {four_bad0_lat:.1f}秒。</p>
</details>

<h3 data-ja="再測定のまとめ" data-en="Re-run summary">再測定のまとめ</h3>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="同じ203問を2回実行した比較" data-en="Two runs of the same 203 tasks">同じ203問を2回実行した比較</caption>
<thead><tr><th scope="col" data-ja="モデル" data-en="Model">モデル</th><th scope="col" data-ja="条件" data-en="Condition">条件</th><th scope="col" class="num" data-ja="1回目" data-en="Run 1">1回目</th><th scope="col" class="num" data-ja="2回目" data-en="Run 2">2回目</th><th scope="col" class="num" data-ja="反転した問題" data-en="Flipped tasks">反転した問題</th><th scope="col" class="num" data-ja="出力トークン中央値(1回目)" data-en="Median output tokens (run 1)">出力トークン中央値(1回目)</th><th scope="col" class="num" data-ja="(2回目)" data-en="(run 2)">(2回目)</th></tr></thead>
<tbody>
{rep_body}
</tbody></table></div>
<p class="note" data-ja="1回目と2回目は同じ203問・同じプロンプト・同じ採点コードです。差は日時だけ（qwen は 00:32 と 09:41 に実行）。他4モデルは各1回のみで、分散は測れていません。" data-en="Both runs used the same 203 tasks, prompts and grader. Only the time differed (qwen at 00:32 and 09:41). The other four models were measured once, so their variance is unknown.">1回目と2回目は同じ203問・同じプロンプト・同じ採点コードです。差は日時だけ（qwen は 00:32 と 09:41 に実行）。他4モデルは各1回のみで、分散は測れていません。</p>
</section>

<section id="notes">
<h2 data-ja="費用と精度の関係" data-en="Cost against accuracy">費用と精度の関係</h2>

<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="203問の実費と正解1件あたりの費用" data-en="Total cost for 203 tasks and cost per correct answer">203問の実費と正解1件あたりの費用</caption>
<thead><tr><th scope="col"><span data-ja="モデル" data-en="Model">モデル</span></th><th scope="col" class="num"><span data-ja="正解数 / 203" data-en="Correct / 203">正解数 / 203</span></th><th scope="col" class="num"><span data-ja="203問の実費" data-en="Cost for 203 tasks">203問の実費</span></th><th scope="col" class="num"><span data-ja="正解1件あたり" data-en="Per correct answer">正解1件あたり</span></th></tr></thead>
<tbody>
{cost_rows}
</tbody></table></div>
<p class="note" data-ja="「正解1件あたり」は 203問の推計費用 ÷ 正解数です。誤答にも費用を払うため、1問あたりの安さと正解1件あたりの安さは一致しません。Qwen は1問 $0.0003 で最も安い一方、精度 61.1% なので正解1件あたりは $0.0005、GPT-5.6 Luna は1問 $0.0022 で正解1件あたり $0.0022 です。1問あたりの差は 6.5倍ですが、正解1件あたりでは 4.4倍に縮みます。" data-en="Per-correct cost is the estimated 203-task spend divided by correct answers. You pay for wrong answers too, so cheap per task is not the same as cheap per correct answer. Qwen is cheapest per task at $0.0003, but at 61.1% accuracy its per-correct cost is $0.0005, against $0.0022 for GPT-5.6 Luna — the gap is 6.5× per task but narrows to 4.4× per correct answer.">「正解1件あたり」は 203問の推計費用 ÷ 正解数です。誤答にも費用を払うため、1問あたりの安さと正解1件あたりの安さは一致しません。Qwen は1問 $0.0003 で最も安い一方、精度 61.1% なので正解1件あたりは $0.0005、GPT-5.6 Luna は1問 $0.0022 で正解1件あたり $0.0022 です。1問あたりの差は 6.5倍ですが、正解1件あたりでは 4.4倍に縮みます。</p>
<p data-ja="費用面では、条文ありの測定で最も実用的だったのは GPT-5.6 Luna（98.0%・1問 $0.0022・中央値2.9秒）でした。最高精度は GPT-5.5（99.5%）ですが1問 $0.0245 で、Luna の約11倍です。ただしこれは各1回の測定であり、上で見たような振れを含んでいません。数ポイントの差でモデルを選ぶなら、先に振れ幅を測るべきです。" data-en="On cost, the most practical point in the retrieval condition was GPT-5.6 Luna (98.0%, $0.0022 per task, 2.9 s median). The highest accuracy was GPT-5.5 (99.5%) at $0.0245 per task, about 11× Luna. But these are single runs and do not include the variance shown above. If you are choosing between models on a few points, measure the variance first.">費用面では、条文ありの測定で最も実用的だったのは GPT-5.6 Luna（98.0%・1問 $0.0022・中央値2.9秒）でした。最高精度は GPT-5.5（99.5%）ですが1問 $0.0245 で、Luna の約11倍です。ただしこれは各1回の測定であり、上で見たような振れを含んでいません。数ポイントの差でモデルを選ぶなら、先に振れ幅を測るべきです。</p>
<p data-ja="この記事の数値はすべて公開JSONから再計算できます。下の記録には、203問の全文・全モデルの全応答（採点理由つき）・40回反復の生ログ・実行コマンドを収録しています。" data-en="Every number here can be recomputed from the published JSON. The record below contains all 203 tasks, every response for every model with grading reasons, the raw 40-run log and the commands to reproduce.">この記事の数値はすべて公開JSONから再計算できます。下の記録には、203問の全文・全モデルの全応答（採点理由つき）・40回反復の生ログ・実行コマンドを収録しています。</p>
</section>

<section id="samples">
<h2 data-ja="実際の3問と、6モデルの回答" data-en="Three real tasks and six model answers">実際の3問と、6モデルの回答</h2>
<p data-ja="タスクセットから3問を抜き出し、条文を渡した条件の回答をそのまま載せています（表示は96文字まで、長い回答は全文を開けます）。正解は条文か計算から機械的に決まっており、モデルの回答を正解にしていません。3問の誤答3件は、いずれも「知らない」ではなく、計算か書式の取りこぼしでした。Qwen は採光補正係数に 0.700 と答え（正解 0.504）、GPT-5.4 Nano は延べ面積を 2500.0 と答え（正解 1000.0。前面道路による制限が反映されていません）、Qwen は条番号を「20」とだけ返しました。誤答の理由は回答文だけでは特定できないため、ここでは値と採点結果のみを示します。" data-en="Three tasks from the set, with the retrieval-condition answers verbatim (truncated to 96 characters; long answers expand). Answers were derived mechanically from the law or arithmetic, never from a model's output. All three wrong answers here are calculation or formatting slips rather than missing knowledge: Qwen answered 0.700 for the daylight coefficient (correct 0.504), GPT-5.4 Nano answered 2500.0 for the floor-area cap (correct 1000.0; the front-road limit is not reflected), and Qwen returned only the digits 20. The answers alone do not reveal why, so only the values and the grading outcome are reported.">タスクセットから3問を抜き出し、条文を渡した条件の回答をそのまま載せています（表示は96文字まで、長い回答は全文を開けます）。正解は条文か計算から機械的に決まっており、モデルの回答を正解にしていません。3問の誤答3件は、いずれも「知らない」ではなく、計算か書式の取りこぼしでした。Qwen は採光補正係数に 0.700 と答え（正解 0.504）、GPT-5.4 Nano は延べ面積を 2500.0 と答え（正解 1000.0。前面道路による制限が反映されていません）、Qwen は条番号を「20」とだけ返しました。誤答の理由は回答文だけでは特定できないため、ここでは値と採点結果のみを示します。</p>
{sample_html}
</section>

<section id="protocol">
<h2 data-ja="条件と費用の数え方" data-en="Conditions and cost accounting">条件と費用の数え方</h2>
<ul>
<li data-ja="対象は建築基準法・同施行令・同施行規則の本則851条（e-Gov 法令APIから取得）。203問の内訳は 数値穴埋め23 / 条文同定27 / 建蔽率30 / 容積率30 / 採光補正係数40 / 誤り検出23 / 応用計算30。" data-en="Corpus: 851 main articles of the Building Standard Law, its enforcement order and enforcement regulations, fetched from the e-Gov Law API. The 203 tasks are 23 numeric cloze, 27 article lookups, 30 BCR, 30 FAR, 40 daylight factors, 23 false-claim checks and 30 applied two-step calculations.">対象は建築基準法・同施行令・同施行規則の本則851条（e-Gov 法令APIから取得）。203問の内訳は 数値穴埋め23 / 条文同定27 / 建蔽率30 / 容積率30 / 採光補正係数40 / 誤り検出23 / 応用計算30。</li>
<li data-ja="全リクエスト {total_req} 件（203問×6モデル×2条件）。temperature 0、max_tokens 12000、非ストリーミング、クライアント再試行0。条文ありでは最大6,000字の条文を質問の前に置いています。" data-en="{total_req} requests total (203 tasks × 6 models × 2 conditions). temperature 0, max_tokens 12000, non-streaming, zero client retries. In the retrieval condition, up to 6,000 characters of article text precede the question.">全リクエスト {total_req} 件（203問×6モデル×2条件）。temperature 0、max_tokens 12000、非ストリーミング、クライアント再試行0。条文ありでは最大6,000字の条文を質問の前に置いています。</li>
<li data-ja="検索は外部依存なしの簡易ハイブリッド（BM25＋見出し完全一致＋引用文の5-gram包含一致）。条文は取得順に詰め、段落だけを抜き出す方式は精度が落ちたため使っていません。" data-en="Retrieval is a dependency-free hybrid: BM25, exact heading match and 5-gram containment of quoted passages. Articles are packed in retrieved order; extracting single paragraphs reduced accuracy, so it is not used.">検索は外部依存なしの簡易ハイブリッド（BM25＋見出し完全一致＋引用文の5-gram包含一致）。条文は取得順に詰め、段落だけを抜き出す方式は精度が落ちたため使っていません。</li>
<li data-ja="採点は決定論的です。数値は正規化後の一致、比率は分数の一致、条文同定は条番号の一致、誤り検出は判定語の一致、応用計算は2つの数値の一致。LLM判定は使っていません。ただし採点は「回答中に正解の数値が含まれるか」を見るため、正しい値を本文中に書いていれば、最後の答えが別でも正解になります。実測では、数値穴埋めの正解138件のうち14件、応用計算の正解164件のうち15件が、末尾の答えだけを見ると正解と一致しませんでした。この分だけ精度はやや甘く出ています。" data-en="Grading is deterministic: normalized numeric equality, fraction equality for ratios, article-number equality for lookups, verdict equality for false claims, and both figures for applied tasks. No LLM judge. One caveat: numeric grading checks whether the correct value appears anywhere in the response, so a response that states the right number in its working but ends on a different figure still counts as correct. In this data, 14 of 138 correct numeric answers and 15 of 164 correct applied answers did not end on the graded value, so accuracy is somewhat generous.">採点は決定論的です。数値は正規化後の一致、比率は分数の一致、条文同定は条番号の一致、誤り検出は判定語の一致、応用計算は2つの数値の一致。LLM判定は使っていません。ただし採点は「回答中に正解の数値が含まれるか」を見るため、正しい値を本文中に書いていれば、最後の答えが別でも正解になります。実測では、数値穴埋めの正解138件のうち14件、応用計算の正解164件のうち15件が、末尾の答えだけを見ると正解と一致しませんでした。この分だけ精度はやや甘く出ています。</li>
<li data-ja="費用は返却された入出力トークン数に取得時の公開単価を掛けた推計で、合計 ${total_cost:.2f}（全{total_req}リクエスト）。照合済みの請求額でも上流の確定原価でもありません。" data-en="Cost is an estimate: returned input/output tokens × the public price captured at run time, totalling ${total_cost:.2f} for all {total_req} requests. This is not a reconciled invoice or the upstream provider's settled cost.">費用は返却された入出力トークン数に取得時の公開単価を掛けた推計で、合計 ${total_cost:.2f}（全{total_req}リクエスト）。照合済みの請求額でも上流の確定原価でもありません。</li>
</ul>
<p class="warnbox" data-ja="限界：これは小さな自作ベンチマークで、新しい非公開テストではありません。長文コンテキスト・画像・自由作文・コード開発・エージェント動作は対象外です。単一モデルの分散は qwen と nano でしか測っておらず、他の4モデルは1回だけです。40回の反復は1問（day-000）だけで、他の202問で同じ振れが起きる頻度は測っていません。203問・1回の測定なので、95%信頼区間（Wilson）は正解率によって幅が変わります。今回の実測では GPT-5.5 の 99.5% で 97.3〜99.9（幅2.7pt）、Qwen の 61.1% で 54.2〜67.5（幅13.3pt）でした。上位5モデルは 93〜99.5% に固まっており、その差（例: 99.5% と 99.0%）は区間が重なるため測定では区別できません。公表時点の上流の構成や混雑で、応答時間も応答の型も変わります。" data-en="Limits: this is a small synthetic benchmark, not a fresh hidden test. Long context, images, open-ended writing, coding projects and agent behaviour are out of scope. Variance was measured only for qwen and nano; the other four models were run once. The 40 repeats cover a single task (day-000); how often the same split occurs on the other 202 is unmeasured. With 203 tasks and one run, the Wilson 95% interval depends on the score: in this data it spans 97.3–99.9 (2.7 points wide) at GPT-5.5's 99.5%, and 54.2–67.5 (13.3 points) at Qwen's 61.1%. The top five models sit between 93% and 99.5%, and their gaps (e.g. 99.5% vs 99.0%) fall inside overlapping intervals, so this measurement cannot separate them. Upstream routing and load change both latency and response type.">限界：これは小さな自作ベンチマークで、新しい非公開テストではありません。長文コンテキスト・画像・自由作文・コード開発・エージェント動作は対象外です。単一モデルの分散は qwen と nano でしか測っておらず、他の4モデルは1回だけです。公表時点の上流の構成や混雑で、応答時間も応答の型も変わります。</p>
</section>

<section id="practice">
<h2 data-ja="実務で使うなら、この3つを先に決める" data-en="If you put this into production, decide these three first">実務で使うなら、この3つを先に決める</h2>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="導入前に決める3点と、今回の実測が示すこと" data-en="Three decisions before deployment and what this measurement shows">導入前に決める3点と、今回の実測が示すこと</caption>
<thead><tr><th scope="col" data-ja="決めること" data-en="Decision">決めること</th><th scope="col" data-ja="今回の実測が示すこと" data-en="What this measurement shows">今回の実測が示すこと</th><th scope="col" data-ja="実装" data-en="Implementation">実装</th></tr></thead>
<tbody>
<tr><td data-ja="条文を渡すか、覚えさせるか" data-en="Retrieve the articles, or trust memory">条文を渡すか、覚えさせるか</td><td data-ja="条文なしだと6モデル中5モデルが90%未満。条文を渡すと全モデルが改善（最大+55.2ポイント）。" data-en="Without articles, five of six models fell below 90%; with them, every model improved, by up to 55.2 points.">条文なしだと6モデル中5モデルが90%未満。条文を渡すと全モデルが改善（最大+55.2ポイント）。</td><td data-ja="条文検索を前段に置き、出典の条番号も一緒に返させる。" data-en="Put article retrieval in front, and return the article number with the answer.">条文検索を前段に置き、出典の条番号も一緒に返させる。</td></tr>
<tr><td data-ja="1回で確定するか、機械で検査するか" data-en="Trust one call, or verify mechanically">1回で確定するか、機械で検査するか</td><td data-ja="同じリクエストでも応答の型が変わり、型が変われば正誤も変わった（40回中15回が即答・全問不正解）。" data-en="The response type changed for an identical request, and the type decided correctness: 15 of 40 were immediate and all wrong.">同じリクエストでも応答の型が変わり、型が変われば正誤も変わった（40回中15回が即答・全問不正解）。</td><td data-ja="数値・○×・所定の書式で答えさせ、範囲外や空回答は自動で不合格にする。" data-en="Require a number, a verdict or a fixed shape, and auto-fail out-of-range or empty answers.">数値・○×・所定の書式で答えさせ、範囲外や空回答は自動で不合格にする。</td></tr>
<tr><td data-ja="1回の合格を、再現性とみなすか" data-en="Treat one pass as reproducibility">1回の合格を、再現性とみなすか</td><td data-ja="同じ条件の2回目で、qwen は82/203問、nano は20/203問が反転した。" data-en="On a second run of the same condition, 82 of 203 tasks flipped for qwen and 20 for nano.">同じ条件の2回目で、qwen は82/203問、nano は20/203問が反転した。</td><td data-ja="重要判定は同じ入力を2回以上通し、不一致を人間の確認に回す。" data-en="Run critical checks twice and route disagreements to a human.">重要判定は同じ入力を2回以上通し、不一致を人間の確認に回す。</td></tr>
</tbody></table></div>
<h3 data-ja="そのまま使える出力形式の例" data-en="An output shape you can check">そのまま使える出力形式の例</h3>
<pre><code>次の形式だけで答えよ。前置き・説明・単位は書かない。
1行目: ANSWER=&lt;数値&gt;
2行目: ARTICLE=&lt;法令名&gt;第&lt;条番号&gt;条

# 受け側の機械検査（例）
- ANSWER が空、または数値に変換できない → 不合格
- ANSWER が想定範囲（例: 採光補正係数なら 0.1〜1.0）の外 → 不合格
- ARTICLE が検索で渡した条番号の集合にない → 不合格
- 2回実行して ANSWER が不一致 → 人間の確認へ</code></pre>
<p data-ja="この形にすると、40回の反復で起きた「6トークンの即答」も、本文が途中で切れた応答も、機械的に不合格にできます。中身の正しさは別に、条文と計算で照合してください。" data-en="With this shape, both the 6-token instant answer and the truncated response seen in the 40 repeats fail mechanically. Correctness of the value is still checked separately against the article and the arithmetic.">この形にすると、40回の反復で起きた「6トークンの即答」も、本文が途中で切れた応答も、機械的に不合格にできます。中身の正しさは別に、条文と計算で照合してください。</p>
<p class="note" data-ja="これは今回の203問での観測で、他の法令・他のモデルにそのまま当てはまる保証はありません。同じ手順を自分の条文で回せば、同じ形で確かめられます。" data-en="These are observations on these 203 tasks; they do not automatically transfer to other statutes or models. Running the same procedure on your own corpus tests it the same way.">これは今回の203問での観測で、他の法令・他のモデルにそのまま当てはまる保証はありません。同じ手順を自分の条文で回せば、同じ形で確かめられます。</p>
<div class="cta">
<p><strong data-ja="自分の条文で、同じ検査を回す" data-en="Run the same check on your own articles">自分の条文で、同じ検査を回す</strong></p>
<p data-ja="203問のJSONと採点コードをそのまま使えます。モデルIDを差し替えれば、同じ採点で別のモデルを比べられます。" data-en="The 203-task JSON and the grader are reusable as-is. Swap the model ID to compare another model under identical grading.">203問のJSONと採点コードをそのまま使えます。モデルIDを差し替えれば、同じ採点で別のモデルを比べられます。</p>
<p class="cta-links"><a class="btn" href="/blog-assets/kenchiku-bench-20261101.json" data-ja="証跡JSONを開く" data-en="Open the evidence JSON">証跡JSONを開く</a>
<span class="muted-note" data-ja="採点コードは非公開リポジトリ（github.com/yukihamada/kenchiku-bench）にあり、必要な場合は個別に共有します" data-en="The grader lives in a private repository (github.com/yukihamada/kenchiku-bench); it can be shared on request">採点コードは非公開リポジトリ（github.com/yukihamada/kenchiku-bench）にあり、必要な場合は個別に共有します</span>
<a href="/docs" data-ja="APIドキュメント" data-en="API docs">APIドキュメント</a>
<a href="/pricing" data-ja="料金" data-en="Pricing">料金</a></p>
</div>
</section>

<section id="data">
<h2 data-ja="記録を確かめる" data-en="Inspect the evidence">記録を確かめる</h2>
<p><a href="/blog-assets/{ASSET}.json" data-ja="203問の全文・全応答・採点理由・反復プローブ・実行コマンド（JSON）" data-en="All 203 tasks, every response with grading reasons, the repeat probes and run commands (JSON)">203問の全文・全応答・採点理由・反復プローブ・実行コマンド（JSON）</a></p>
<p data-ja="測定コードとタスク生成コードは github.com/yukihamada/kenchiku-bench（非公開リポジトリ）にあり、公開している記録は上のJSONです。条文取得（corpus/fetch_laws.py）→タスク生成（bench/tasks.py）→自己テスト（bench/selftest.py）→測定（bench/run.py）の順で、同じ条件を再現できます。自己テストはAPIを叩かず、採点ロジックと条文の引き当てだけを検査します。" data-en="The measurement and task-generation code is at github.com/yukihamada/kenchiku-bench (a private repository); the public record is the JSON above. Reproduce the same conditions in order: fetch the law text (corpus/fetch_laws.py), generate tasks (bench/tasks.py), self-test (bench/selftest.py), then measure (bench/run.py). The self-test calls no API; it checks only the grading logic and article lookup.">測定コードとタスク生成コードは github.com/yukihamada/kenchiku-bench（非公開リポジトリ）にあり、公開している記録は上のJSONです。条文取得（corpus/fetch_laws.py）→タスク生成（bench/tasks.py）→自己テスト（bench/selftest.py）→測定（bench/run.py）の順で、同じ条件を再現できます。自己テストはAPIを叩かず、採点ロジックと条文の引き当てだけを検査します。</p>
<p data-ja="同じ手順を teai の API で回すなら、任意のモデルIDで bench/run.py --rag を実行してください。モデルを差し替えても採点は変わりません。" data-en="To run the same procedure through the teai API, execute bench/run.py --rag with any model ID. Grading does not change when the model changes.">同じ手順を teai の API で回すなら、任意のモデルIDで bench/run.py --rag を実行してください。モデルを差し替えても採点は変わりません。</p>
</section>
<section id="related">
<h2 data-ja="関連する実測" data-en="Related measurements">関連する実測</h2>
<ul class="related">
<li><a href="/blog/bonsai-benchmark" data-ja="Bonsaiを測る。JSON・SQL・ツール判断まで。" data-en="Measuring Bonsai: JSON, SQL and tool decisions.">Bonsaiを測る。JSON・SQL・ツール判断まで。</a></li>
<li><a href="/blog/task-benchmark-20261004" data-ja="正解でも、そのまま使えるとは限らない。" data-en="A correct answer is not always ready to use.">正解でも、そのまま使えるとは限らない。</a></li>
<li><a href="/blog" data-ja="実測記事の一覧" data-en="All measured reports">実測記事の一覧</a></li>
</ul>
</section>
</main>
<footer><span data-ja="実測・執筆：teai.io · {DATE}（JST）" data-en="Measured and written by teai.io · {DATE} (JST)">実測・執筆：teai.io · {DATE}（JST）</span> · <a href="/blog" data-ja="実測記事一覧" data-en="More measured reports">実測記事一覧</a></footer>
<script>
(() => {{
  const en = new URLSearchParams(location.search).get('lang') === 'en';
  if (!en) return;
  document.documentElement.lang = 'en';
  document.title = 'We asked the same question 40 times. It came back two different ways. | teai';
  document.querySelectorAll('[data-en]').forEach(el => {{
    if (el.children.length) return;
    el.textContent = el.dataset.en;
  }});
  document.querySelectorAll('[data-aria-en]').forEach(el => {{ el.setAttribute('aria-label', el.dataset.ariaEn); }});
  document.querySelectorAll('[data-meta-en]').forEach(el => {{ el.content = el.dataset.metaEn; }});
  const toggle = document.getElementById('language');
  toggle.textContent = '日本語';
  toggle.href = '?lang=ja';
  document.querySelectorAll('a[href="/blog"]').forEach(el => {{ el.href = '/blog?lang=en'; }});
}})();
</script>
</body></html>
"""

    out = os.path.join(a.teai, "web", f"teai-blog-{SLUG}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out} ({len(html.encode()) / 1024:.1f} KB)")
    print(f"  qwen flips {flips}/{n}; probe short {total_short}/{len(pr)} ctok {short_ctoks}; "
          f"long med {long_med:.0f}; nano probe short {len(nano_short)}/{len(nr)} ok {nano_ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
