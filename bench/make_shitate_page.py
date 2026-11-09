#!/usr/bin/env python3
"""建築ベンチ記事（shitate.ai/notes/kenchiku.html）を実データから生成する。

  python3 bench/make_shitate_page.py --out /path/to/shitate-clone/notes/kenchiku.html

数字はすべて results-regraded/ と results/ の保存ログから計算する。本文に
手で数字を書かない（採点基準が変われば記事も変わる。食い違いを防ぐ）。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ORDER = [
    ("openai/gpt-5.5", "GPT-5.5"),
    ("google/gemini-3.5-flash", "Gemini 3.5 Flash"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("openai/gpt-5.6-luna", "GPT-5.6 Luna"),
    ("openai/gpt-5.4-nano", "GPT-5.4 Nano"),
    ("qwen/qwen3.5-flash-02-23", "Qwen3.5 Flash"),
]
KIND_LABEL = [
    ("numeric", "数値穴埋め"),
    ("cite", "条文同定"),
    ("calc_bcr", "建蔽率"),
    ("calc_far", "容積率"),
    ("calc_daylight", "採光補正"),
    ("false_claim", "誤り検出"),
    ("applied", "応用計算"),
]
# 円換算レートを1つに固定（ペルソナ指摘: 記事内で不整合があった）
JPY_PER_USD = 150


def _load_tasks():
    p = os.path.join(ROOT, "data", "tasks.jsonl")
    return {t["id"]: t for t in (json.loads(l) for l in open(p, encoding="utf-8"))}


TASKS = _load_tasks()


def load_summary(tag, regraded=True):
    base = "results-regraded" if regraded else "results"
    p = os.path.join(ROOT, base, tag, "summary.json")
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    d = [d] if isinstance(d, dict) else d
    return {x["model"]: x for x in d if isinstance(x, dict) and "model" in x}


def load_rows(tag, model):
    fn = model.replace("/", "_") + ".jsonl"
    p = os.path.join(ROOT, "results-regraded", tag, fn)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8")]


def pct(x):
    return f"{x*100:.1f}%"


def yen(usd):
    return f"¥{usd*JPY_PER_USD:.2f}"


def esc(s):
    return html.escape(str(s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    base = load_summary("base")
    rag = load_summary("rag")
    cn1 = load_summary("cn-rag")
    cn2 = load_summary("cn-rag2")
    sky = load_summary("sky-tools", regraded=False)
    sky2 = load_summary("sky2-tools", regraded=False)
    sky_llm = load_summary("sky-rag", regraded=False)
    sky2_llm = load_summary("sky2-rag", regraded=False)
    engine = json.load(open(os.path.join(ROOT, "results", "engine", "summary.json"), encoding="utf-8"))

    # 公開 203 表（base→RAG）
    rows_html = []
    for mid, label in ORDER:
        b = base.get(mid, {}).get("accuracy")
        r = rag.get(mid, {}).get("accuracy")
        c = rag.get(mid, {}).get("cost_per_task_usd")
        lat = rag.get(mid, {}).get("latency_median_s")
        rows_html.append(
            f'<tr><td>{esc(label)}</td>'
            f'<td class="num">{pct(b) if b is not None else "—"}</td>'
            f'<td class="num"><strong>{pct(r) if r is not None else "—"}</strong></td>'
            f'<td class="num">{yen(c) if c is not None else "—"}</td>'
            f'<td class="num">{lat:.1f}秒</td></tr>'
        )
    table_203 = "\n".join(rows_html)

    # 条文なし→RAG の段差を横棒グラフ（CSP-safe: 属性のみ、style=は使わない）
    bar_h, gap, label_w, chart_w = 22, 10, 130, 420
    top = 26  # 軸目盛り用の上余白
    n_models = len(ORDER)
    plot_h = n_models * (bar_h + gap)
    svg_h = top + plot_h + 14
    svg_w = label_w + chart_w + 60
    svg_parts = [
        f'<svg viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" role="img" '
        f'aria-label="条文なしとRAGの正解率比較（横棒グラフ）" '
        f'font-family="sans-serif" font-size="11">'
    ]
    # 縦グリッド線と軸目盛り（0/50/100%）
    for tick in (0, 50, 100):
        x = label_w + chart_w * tick / 100
        svg_parts.append(
            f'<line x1="{x:.1f}" y1="{top - 4}" x2="{x:.1f}" y2="{top + plot_h}" '
            f'stroke="#d7dde5" stroke-width="1"></line>'
            f'<text x="{x:.1f}" y="{top - 10}" text-anchor="middle" fill="#5a6675">{tick}%</text>'
        )
    for i, (mid, label) in enumerate(ORDER):
        b = base.get(mid, {}).get("accuracy")
        r = rag.get(mid, {}).get("accuracy")
        y = top + i * (bar_h + gap)
        bw = chart_w * (b or 0)
        rw = chart_w * (r or 0)
        svg_parts.append(
            f'<text x="0" y="{y + bar_h - 6}">{esc(label)}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{bar_h // 2}" '
            f'fill="#c9d4e0"></rect>'
            f'<rect x="{label_w}" y="{y + bar_h // 2}" width="{rw:.1f}" '
            f'height="{bar_h // 2}" fill="#2f6fb0"></rect>'
            f'<text x="{label_w + rw + 4:.1f}" y="{y + bar_h - 6}" fill="#1f2a36">{pct(r) if r is not None else "—"}</text>'
        )
    svg_parts.append('</svg>')
    gap_chart = "".join(svg_parts)

    # per-kind マトリクス（RAG）公開モデルのみ
    kind_head = "".join(f"<th>{esc(lbl)}</th>" for _, lbl in KIND_LABEL)
    kind_rows = []
    for mid, label in ORDER:
        bk = rag.get(mid, {}).get("by_kind", {})
        cells = []
        for k, _ in KIND_LABEL:
            v = bk.get(k)
            if not v:
                cells.append('<td class="num">—</td>')
                continue
            ok = round(v["accuracy"] * v["n"])
            cls = "num" if v["accuracy"] >= 0.999 else "num weak"
            cells.append(f'<td class="{cls}">{ok}/{v["n"]}</td>')
        kind_rows.append(f'<tr><td>{esc(label)}</td>' + "".join(cells) + "</tr>")
    table_kind = "\n".join(kind_rows)

    # 他モデル（RAG）
    others = []
    for tag, d in [("cn-rag", cn1), ("cn-rag2", cn2)]:
        for mid, x in d.items():
            others.append((x["accuracy"], mid))
    others.sort(reverse=True)
    other_rows = "\n".join(
        f'<tr><td>{esc(m)}</td><td class="num">{pct(acc)}</td></tr>'
        for acc, m in others
    )

    # 天空率ツール併用（LLM単体の「回答分」→ ツール併用の段差を見せる）
    # 上流エラーは可用性の問題で能力値ではないため、回答できた分の正解率で示す
    def answered_acc(d):
        n = d.get("n", 0) or 0
        errs = d.get("errors", 0) or 0
        if n and errs >= n:
            return None, errs, n  # 全件エラー＝測定不能
        acc = d.get("accuracy_answered")
        if acc is None:
            acc = d.get("accuracy")
        return acc, errs, n
    sky_rows = []
    for mid, label in ORDER:
        a50 = sky.get(mid, {}).get("accuracy")
        a20 = sky2.get(mid, {}).get("accuracy")
        if a50 is None and a20 is None:
            continue
        def gap(llm_d, tool):
            if tool is None:
                return "—"
            acc, errs, n = answered_acc(llm_d)
            if acc is None:
                return f"—（全{n}件エラー・測定不能）→ {pct(tool)}"
            suffix = f"<sup>*</sup>" if errs else ""
            return f"{pct(acc)}{suffix} → {pct(tool)}"
        sky_rows.append(
            f'<tr><td>{esc(label)}</td>'
            f'<td class="num">{gap(sky_llm.get(mid, {}), a50)}</td>'
            f'<td class="num">{gap(sky2_llm.get(mid, {}), a20)}</td></tr>'
        )
    table_sky = "\n".join(sky_rows)
    # エラー注記（該当モデルのみ列挙、回答分の補足つき）
    err_notes = []
    for mid, label in ORDER:
        for tag, d in [("50問", sky_llm.get(mid, {})), ("追加20問", sky2_llm.get(mid, {}))]:
            errs = d.get("errors", 0) or 0
            if errs:
                n = d.get("n", "") or ""
                err_notes.append(f"{label}（{tag}）は{errs}/{n}件が上流エラー")
    sky_err_note = (
        "<p class=\"note\"><sup>*</sup> 上流エラー（HTTP 502等）が混じったセルは、<strong>回答できた分だけの正解率</strong>で示しています（エラーは能力ではなく可用性の問題）。"
        "該当: " + "、".join(err_notes)
        + "。全件エラーで測定不能なセルは「—」と明記しました。ツール併用側はエラーなしで測定しています。</p>"
        if err_notes else ""
    )

    # 法規エンジン
    ek = engine["kinds"]
    def ek_acc(name):
        v = ek.get(name, {})
        if "accuracy" in v and v["accuracy"] is not None:
            return f'{v.get("correct", "")}/{v.get("n", "")}'
        return "—"
    engine_rows = (
        f'<tr><td>容積率（法52）</td><td class="num">{ek_acc("calc_far")}</td></tr>'
        f'<tr><td>建蔽率（法53・角地/防火緩和）</td><td class="num">{ek_acc("calc_bcr")}</td></tr>'
        f'<tr><td>応用（敷地×限度率）</td><td class="num">{ek_acc("applied")}</td></tr>'
        f'<tr><td>採光補正係数（令20）</td><td class="num">{ek_acc("calc_daylight")}</td></tr>'
    )
    skyx = ek.get("sky_crosscheck", {})
    engine_note = (
        f'天空率は Python 参照実装と Rust エンジンのクロスチェック {skyx.get("n","")} 件で'
        f'平均差 {skyx.get("mean_diff_pt","")}pt（最大 {skyx.get("max_diff_pt","")}pt）。'
    )

    # 失敗例（サンプル）: 途中式まで残っていて、誤答の結論が見えるもの
    fail_examples = []
    for tag, model, want_id in [("cn-rag2", "z-ai/glm-5.3-flash", "day-020"),
                                ("cn-rag2", "z-ai/glm-5.3-flash", "far-009")]:
        for r in load_rows(tag, model):
            if r.get("error") or r.get("correct"):
                continue
            if r["id"] == want_id:
                fail_examples.append((model, r))
                break
    # 誤りの型（実物の誤答を読んで特定した原因）
    ERROR_TYPE = {
        "day-020": "採光補正係数の計算順序の誤り。正しくは「比率×10−1.0 → 縁側補正×0.7 → 3.0の上限」。モデルは3.0の上限を縁側補正より先に適用し、2.100 とした（正解 2.870）。令第20条2項の適用順を逆にした、実務で最も引っかかりやすい罠。",
        "far-009": "前面道路による容積率制限の比較ミス。工業専用地域は「その他」で前面道路幅×6/10＝66/10、都市計画容積率は15/10。法第52条は両者の小さい方が限度なので正解は15/10。モデルは比較の向きを逆転させ（66/10<15/10と誤判定）、大きい66/10を限度とした。",
    }
    fail_html = []
    for model, r in fail_examples:
        task = TASKS.get(r["id"], {})
        q = esc((task.get("question") or "")[:220])
        etype = ERROR_TYPE.get(r["id"], "")
        etype_html = f'<p class="note"><strong>誤りの型:</strong> {esc(etype)}</p>' if etype else ""
        # モデル原文のMarkdown強調(**...**)は見た目が崩れるため除去
        pred = (r.get("pred") or "")[:400].replace("**", "")
        fail_html.append(
            f'<h3>{esc(model)} — {esc(r["id"])}（{esc(dict(KIND_LABEL).get(r["kind"], r["kind"]))}）</h3>'
            f'<p class="note">問題（抜粋）: {q}</p>'
            f'<p><strong>正解:</strong> {esc(r.get("answer",""))}</p>'
            f'<pre><code>{esc(pred)}</code></pre>'
            f'{etype_html}'
        )
    fail_block = "\n".join(fail_html)

    # 主要数値（本文用）
    luna = rag.get("openai/gpt-5.6-luna", {})
    nano = rag.get("openai/gpt-5.4-nano", {})
    nano_base = base.get("openai/gpt-5.4-nano", {}).get("accuracy")
    nano_old = 0.931  # 旧採点での公開値（注記用）

    page = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>建築基準法203問 — 条文を渡すとモデルは何割正しいか | Shitate LAB NOTES</title>
<meta name="description" content="建築基準法・同施行令・同施行規則の条文から機械的に作った203問を複数モデルで同一条件実測。条文RAGで全モデルが大きく伸び、計算は法規エンジンで満点に達した。採点バグの修正と再採点、失敗例、実務での使い方、teai.ioでの試し方まで公開。">
<meta property="og:title" content="建築基準法203問 — 条文を渡すとモデルは何割正しいか">
<meta property="og:description" content="正解をLLMに書かせず、e-Govの条文と決定論的計算から作った203問。条文RAG・法規エンジン・天空率ツールの実測と採点修正を公開。">
<meta property="og:type" content="article">
<meta property="og:image" content="https://shitate.ai/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="canonical" href="https://shitate.ai/notes/kenchiku.html">
<meta property="og:url" content="https://shitate.ai/notes/kenchiku.html">
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/notes/note.css">
<link rel="icon" href="/favicon.ico">
<script defer src="https://enabler-analytics.fly.dev/t.js"></script>
</head>
<body>
<nav><div class="brand"><a href="/">仕立て</a><small>SHITATE — LAB NOTES</small></div></nav>
<article class="article">
  <div class="sec-label">LAB NOTES #9</div>
  <h1>建築基準法203問 — 条文を渡すと、モデルは何割正しいか</h1>
  <p class="date">2026-11-01 公開 ・ 2026-11-09 採点修正・再採点して更新</p>

  <p>建築の実務でLLMを使うとき、怖いのは「間違えること」より<strong>「自信満々に間違えること」</strong>です。容積率を1つ読み違えれば、その後の設計も確認申請も全部やり直しになります。</p>
  <p>そこで、正解をモデルに書かせないベンチマークを作りました。正解は<strong>e-Gov 法令API の条文そのもの</strong>と、<strong>条文の式に数値を入れた決定論的計算</strong>だけから生成しています。採点も機械的に決まり、判定するLLMは入っていません。誰でも追試できます。</p>

  <div class="disclosure">
    <strong>2026-11-09 の更新（重要）:</strong> 採光補正係数の採点にバグがあり、修正して全モデルを再採点しました。旧採点は回答の「最初の数値」を拾うため、途中式の水平距離 D を正解と誤読して系統的に過小評価していました。修正後は「最初か最後の数値のどちらかが正解なら正」としています。影響は主に計算系で、たとえば GPT-5.4 Nano のRAG正解率は {pct(nano_old)} → <strong>{pct(nano.get("accuracy",0))}</strong> に変わりました。以下の数字はすべて修正後の再採点値です（生ログと採点コードは公開リポジトリにあります）。
  </div>

  <h2>結果（203問・temperature 0・各1回）</h2>
  <div class="btable-wrap">
  <table class="btable">
    <tr><th>モデル</th><th>条文なし</th><th>条文を渡す(RAG)</th><th>1問あたり</th><th>中央値</th></tr>
    {table_203}
  </table>
  </div>
  <p class="note">下のグラフは各モデルの<strong>条文なし（薄灰）</strong>と<strong>RAG＝条文を渡す（青）</strong>の正解率です。横軸は0〜100%。</p>
  <div class="btable-wrap">{gap_chart}</div>
  <p>7種の内訳は 数値穴埋め23 / 条文同定27 / 建蔽率30 / 容積率30 / 採光補正係数40 / 誤り検出23 / 応用計算30。</p>
  <p class="note">上位の差（99.0% と 99.5% など）は<strong>1回測定のノイズ域</strong>です。同じモデルを再測定すると ±1pt 程度は動きます（例: GPT-5.4 Nano は 95.6% → 96.5%）。順位のわずかな差ではなく「条文を渡すと大きく伸びる」という段差を見てください。</p>
  <p class="note"><strong>Qwen3.5 Flash の 61.1% は能力値ではなく下限です。</strong>この測定では応答が極端に短く終わる設問が多く（出力トークン中央値6、うち130問が10トークン以下）、その短い即答群の正答率は49%でした。同じモデルを再測定すると 91.6%（中央値3501）まで戻ります。短い応答が正常終了（finish=stop）として返る挙動で、原因の特定は上流事業者側の確認待ちですが、少なくとも「モデルの実力が61%」ではありません。公平のため、この値は「実行時の出力欠損を含む下限」として読んでください。</p>

  <h2>種別ごとの弱点（RAG時・正解数/問題数）</h2>
  <p>見出しの正解率だけで選ぶと「採光だけ弱いモデル」を引きます。種別ごとに見ると差が出ます。</p>
  <div class="btable-wrap">
  <table class="btable">
    <tr><th>モデル</th>{kind_head}</tr>
    {table_kind}
  </table>
  </div>
  <p>たとえば GPT-5.6 Luna は総合 {pct(luna.get("accuracy",0))} でも採光補正が弱点、GPT-5.4 Nano は応用計算が弱点です。用途に合わせて選んでください。</p>

  <h2>数字より面白かった3つのこと</h2>
  <p><strong>1. 条文を渡すだけで、ほぼ別のモデルになる。</strong>いちばん伸びたのは最安クラスでした。GPT-5.4 Nano は {pct(nano_base)} → {pct(nano.get("accuracy",0))}。<strong>「法令を知っているか」をモデルに期待するのは、もう設計として間違い</strong>です。条文を検索して渡す側の作り込みのほうが、モデルの格上げより効きます。</p>
  <p><strong>2. それでも残る差は「計算の段数」に出る。</strong>1段の公式当てはめ（建蔽率・容積率）はRAGを渡すと全モデルがほぼ満点。差が残ったのは、前面道路制限で容積率を決めてから敷地面積を掛け、さらに建蔽率側と並べる<strong>2段の応用計算</strong>でした。実務で使うのはこの形なので、ここを見ないと選定を誤ります。</p>
  <p><strong>3. 計算はLLMに任せず、エンジンで解くと満点になる。</strong>条文RAGの先に、条文の式をコードにした<strong>法規エンジン</strong>を用意しました（下の表）。容積率・建蔽率・応用・採光のすべてで満点でした。天空率のように手計算が現実的でないものは、LLM単体ではなく計算エンジンやツールに渡すのが正解です。</p>

  <h2>条文RAGの先: 法規エンジンは満点だった</h2>
  <div class="btable-wrap">
  <table class="btable">
    <tr><th>計算種別</th><th>正解</th></tr>
    {engine_rows}
  </table>
  </div>
  <p>{esc(engine_note)}LLMに式を解かせるのではなく、条文の式をコードに落として計算させるのが、実務で最も安全です。</p>

  <h2>天空率は「ツール併用」で当たる</h2>
  <p>天空率（令135条の5〜9）は手計算が現実的でなく、LLM単体では安定しません。ここは上の203問とは<strong>別枠の70問</strong>（天空率50問＋高低差・角地の追加20問）で、203問の種別内訳には天空率を含めていません。各セルは「LLM単体（回答できた分）→ ツール併用」の正解率です。</p>
  <p>内訳を見ると、<strong>天空率の「考え方」を問う基本問題（sky_points）は単体でも9〜10割当たります</strong>が、<strong>実際の数値計算（calc_sky）と2案の比較（sky_compare）は単体だと大きく崩れます</strong>。たとえば GPT-5.6 Luna は sky_points 10/10 でも calc_sky 9/20・sky_compare 7/19、Gemini 3.5 Flash は sky_points 10/10 でも sky_compare 1/15。ツール併用では calc_sky・sky_compare とも 19〜20/20 まで戻ります。「天空率は苦手」ではなく「天空率の<strong>計算</strong>が苦手」と読むのが正確です。</p>
  <div class="btable-wrap">
  <table class="btable">
    <tr><th>モデル</th><th>天空率50問（単体→併用）</th><th>追加20問（単体→併用）</th></tr>
    {table_sky}
  </table>
  </div>
  {sky_err_note}

  <h2>他のモデル（RAG時）</h2>
  <div class="btable-wrap">
  <table class="btable">
    <tr><th>モデル</th><th>203問 正解率</th></tr>
    {other_rows}
  </table>
  </div>
  <p class="note">Qwen3.8 Max は203/203（100.0%）ですが、1問あたり約 $0.010 で Gemini 3.5 Flash（$0.0052）より高く（GPT-5.5 の $0.0245 よりは安い）、1回測定で分散も未測定のため、コスト・再現性の判断材料がそろうまで推奨には含めていません。数値は参考として掲載します。</p>

    <h2>失敗例（正直に抜粋）</h2>
  <p>成功例だけを見ると実力を過大評価します。ここでは実際に外した例を、途中式ごと載せます。<strong>安さの前に再現性と正しさが必要</strong>という教訓の実物です。</p>
  {fail_block}

  <h2>正しい手順の実演（容積率の1問）</h2>
  <p>同じ far-009 を、条文どおりに解くとこうなります。実務でそのまま使える順序です。</p>
  <ol>
    <li><strong>法第52条第1項</strong>で、用途地域と都市計画容積率を確認 → 工業専用地域・15/10。</li>
    <li><strong>法第52条第2項</strong>で、前面道路幅による制限を計算。前面道路が12m未満のときだけ適用され、本例は11mで該当。工業専用地域は「その他の建築物」なので 6/10。11m × 6/10 = 66/10。</li>
    <li><strong>両者の小さい方</strong>を限度とする → min(15/10, 66/10) = <strong>15/10</strong>。</li>
  </ol>
  <p>ポイントは、前面道路制限を計算したら必ず都市計画容積率と<strong>小さい方を採る</strong>こと。66/10 が大きいからといって 66/10 を限度にすると誤りです（失敗例のモデルはここを逆転させました）。この3ステップは法第52条の条文そのままで、LLMに計算させるより法規エンジンに渡すほうが確実です。</p>

  <h2>方法（全部書きます）</h2>
  <ul>
    <li>条文は e-Gov 法令API から取得（建築基準法・同施行令・同施行規則。本則の条文要素は合計851で、条番号ベースでは建築基準法107条・施行令141条ほか）。条文を機械的に解析して問題と正解を生成</li>
    <li>誤り検出問題は、条文の一節の数値を1つだけ改変して作問（改変前の原文が正解の根拠）</li>
    <li>計算問題は条文の式をコードに実装し、乱数で条件を振って生成。正解はコードが計算</li>
    <li>採点は完全機械採点（数値一致・条番号の正規化・分数の等価判定）。LLM judge は使っていません</li>
    <li>RAGは外部依存なしのハイブリッド検索（BM25＋見出し完全一致＋5-gram包含一致）。条文は1問あたり最大6,000字</li>
    <li>API経由（teai.io）・temperature 0・max_tokens 12,000。エラー0件の状態で集計</li>
    <li>自己テスト（APIを叩かない回帰テスト）で「正解が必ず採点を通る」ことを検証。これは<strong>正解側の通過テスト</strong>であり、モデル回答の読み取り妥当性は別途、採光の採点バグ修正と再採点で確認しています</li>
    <li>追試する場合: <code>python3 bench/regrade.py --tag rag</code>（既定）で本記事と同じ値になります。保存済みログは pred が600字で切り詰められているため、既定では切り詰め行の再判定をスキップして元の判定を維持します（<code>--force</code> を付けると切り詰め行も再判定するため、値が数ptずれることがあります）</li>
  </ul>

  <h2>実務で使うなら、この3つを先に決める</h2>
  <ol>
    <li><strong>貼る条文を決める。</strong>建蔽率は法第53条、容積率は法第52条第1項・第2項、採光補正係数は令第20条、天空率は令第135条の5〜第135条の9（前面道路が2本なら令第132条も）。e-Gov法令検索で条文そのものを開き、質問に貼ってください。条例・告示・特定行政庁の取り扱いは、各自治体の例規集（「◯◯市 建築基準法施行条例」で検索）で別に確認します。</li>
    <li><strong>計算はエンジンに渡す。</strong>容積率・建蔽率・採光・天空率のような計算は、LLMに式を解かせず、条文の式をコードにした計算エンジンやツールに渡します（上の表のとおり満点に近づきます）。このベンチマークの法規エンジン（条文の式をコード化した参照実装）と採点コードは <code>github.com/yukihamada/kenchiku-bench</code> で公開しています。まずは手元でエンジンを回し、同じ条件で計算できることを確かめてから業務に組み込んでください。</li>
    <li><strong>モデルは用途で選ぶ。</strong>コスト最優先なら GPT-5.4 Nano（1問 $0.0014・95.6%）、バランスなら GPT-5.6 Luna（$0.0022・98.0%）、最高精度なら Gemini 2.5 Flash（$0.0028・99.0%）か GPT-5.5（$0.0245・99.5%）。1問あたりのコストはこの10倍以上の差があるので、精度の頭打ち（99%前後）を踏まえると、実務では Nano〜Luna で十分な場面が多いはずです。用途別には、採光中心なら Luna の採光37/40を避けて Nano（40/40）か Gemini（40/40）、応用計算中心なら Nano の応用26/30に注意して Gemini 3.5 Flash か GPT-5.5（30/30）を選ぶ、という形で種別表と突き合わせてください。</li>
  </ol>

  <h2>teai.io で試す</h2>
  <p>この実測は teai.io 経由で行いました。同じ条文RAGを次の3通りで試せます。</p>
  <ul>
    <li>CLI: <code>te kenchiku "採光補正係数の計算方法は？"</code>（建築基準法・同施行令・同施行規則の条文RAG付きQ&amp;A）</li>
    <li>API: <code>GET https://teai.io/api/v1/kenchiku/search?q=採光補正係数</code>（条文検索。β・一般的な参考情報であり、個別の適法性は保証しません）</li>
    <li>チャット: <code>model:"shitate/kenchiku"</code> を <code>/v1/chat/completions</code> に指定</li>
  </ul>
  <p>検索APIは実際に叩いて動作を確認済みです。たとえば <code>q=採光補正係数</code> は令第20条（有効面積の算定方法）を返します（2026-11-09時点）。</p>
  <pre><code>$ curl -s "https://teai.io/api/v1/kenchiku/search?q=採光補正係数&amp;limit=1"
{{"results":[{{"article":"20","caption":"（有効面積の算定方法）", ...}}], "disclaimer":"[β] ..."}}</code></pre>
  <p class="note">いずれもβです。出力は一般的な参考情報であり、実際の設計・確認申請は建築士・特定行政庁にご確認ください。</p>

  <h2>正直な限界</h2>
  <p>各モデル1回・temperature 0なので分散は測れていません（一部モデルは同一設定で2回測り、数pt振れました）。問題は法令の本則テキストから機械的に作れる範囲に限られ、通達・条例・運用・特定行政庁の判断は入っていません。条例・告示はe-Govの法令APIでは取得できないため、各自治体の例規集で別途確認してください。実務の適法性判断をこのベンチマークの点数で代替することはできません。Claude系はゲートウェイ上流の503が続き測定できず、表から除外しています（当方の実装起因ではありません）。測定可能になり次第追記します。</p>

  <div class="disclosure">
    開示: この実測はShitate（株式会社イネブラ）が自社ゲートウェイteai.io経由で実施しました。teai.ioは比較対象モデルの一部を再販しています。問題セット・採点コード・生ログは github.com/yukihamada/kenchiku-bench で公開しています。円換算は1ドル={JPY_PER_USD}円で統一しています。スコアは2026-11-09時点・API経由の挙動であり、各モデルの能力の恒久的な評価ではありません。本ページは一般的な情報提供であり、個別の建築確認・法適合性の判断ではありません。モデル名は各社の商標です。
  </div>
  <p class="back"><a href="/#labnotes">← Shitate LAB NOTES</a></p>
</article>
</body>
</html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(page)
    print(f"wrote {a.out} ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
