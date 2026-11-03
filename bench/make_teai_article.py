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
UPDATED = "2026-11-03"
SKY_ASSET = "kenchiku-bench-sky-20261103"

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


SKY_ORDER = [
    ("openai/gpt-5.6-luna", "GPT-5.6 Luna"),
    ("google/gemini-3.5-flash", "Gemini 3.5 Flash"),
    ("openai/gpt-5.4-nano", "GPT-5.4 Nano"),
    ("openai/gpt-5.5", "GPT-5.5"),
]
SKY_KIND = {"calc_sky": ("天空率の計算", "Sky factor value"),
            "sky_compare": ("計画と適合建築物の比較", "Planned vs conforming"),
            "sky_points": ("測定点の数と間隔", "Measurement points")}


def strict_scores() -> dict:
    """条文ありの結果を「回答の最後の答えだけ」で厳しく採点し直した正解率（数値穴埋め・応用計算のみ厳しくする）。"""
    import re as _re
    from bench.grade import numeric_candidates
    tasks = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8")}
    out = {}
    for m, _ in ORDER:
        rows = list(load("rag", m).values())
        ok = sum(1 for r in rows if r["correct"])
        demote = 0
        for r in rows:
            if not r["correct"] or "pred" not in r:
                continue
            t, p = tasks[r["id"]], r["pred"].strip()
            if t["kind"] == "numeric":
                frags = [f for f in _re.split(r"[\n。]", p) if numeric_candidates(f)]
                if t["answer_norm"] not in numeric_candidates(frags[-1] if frags else ""):
                    demote += 1
            elif t["kind"] == "applied":
                _, a, b = t["answer_norm"].split("#")
                nums = [float(x) for x in _re.findall(r"\d+(?:\.\d+)?", p.replace(",", ""))][-2:]
                if not (any(abs(v - float(a)) <= 0.11 for v in nums) and any(abs(v - float(b)) <= 0.11 for v in nums)):
                    demote += 1
        out[m] = ((ok - demote) / len(rows), ok / len(rows))
    return out


def manual_stats() -> dict:
    """人が条文を貼った場合（bench/run_manual.py）と、同じ問題の条文なし・自動検索の正解数。"""
    out = {}
    for s in json.load(open(os.path.join(ROOT, "results", "manual", "summary.json"), encoding="utf-8")):
        m, ids = s["model"], set(s["ids"])
        def cnt(tag):
            rows = [r for r in load(tag, m).values() if r["id"] in ids]
            return sum(1 for r in rows if r["correct"]), len(rows)
        man = list(load("manual", m).values())
        out[m] = {"base": cnt("base"), "rag": cnt("rag"),
                  "manual": (sum(1 for r in man if r["correct"]), len(man)),
                  "wrong": [r for r in man if not r["correct"]]}
    return out


def two_run_stats() -> tuple[int, int, int, int]:
    """同じ条件で2回測った結果から「2回の答えが食い違った問題に、1回目の誤答が何件含まれたか」を数える。
    答えは回答末尾の数値・条番号・判定語を機械的に抜き出して比べる（書き方の違いも食い違いに数える）。"""
    import re as _re

    def key(r):
        p = (r.get("pred") or "").replace(",", "").replace("，", "")
        toks = _re.findall(r"第?\d+(?:\.\d+)?(?:条(?:の\d+)?)?|適合|不適合|誤り|正しい", p)
        return tuple(toks[-2:]) if toks else None
    wrong = caught = 0
    lo = hi = None
    for a, b, m in (("rag", "rag-repeat1", "qwen/qwen3.5-flash-02-23"), ("rag", "rag-repeat2", "openai/gpt-5.4-nano")):
        A, B = load(a, m), load(b, m)
        w = c = 0
        for k, x in A.items():
            y = B.get(k)
            if not y or "pred" not in x or "pred" not in y or x["correct"]:
                continue
            w += 1
            c += not (key(x) == key(y) and key(x) is not None)
        wrong += w
        caught += c
        r = round(c / w * 100)
        lo = r if lo is None else min(lo, r)
        hi = r if hi is None else max(hi, r)
    return wrong, caught, lo, hi


def build_use(teai: str, skip_asset: bool) -> tuple[str, str]:
    """「この記事をどう使うか」節。立場別の具体的な手順と、配布ツール sky_tool.py。"""
    tools = summary("sky-tools")
    rag = summary("sky-rag")
    luna_t, luna_r = tools["openai/gpt-5.6-luna"], rag["openai/gpt-5.6-luna"]
    demo = json.load(open(os.path.join(ROOT, "results", "sky-tools-demo.json"), encoding="utf-8"))
    import re as _re
    m = _re.search(r"天空率\s*([0-9.]+)\s*[％%]", demo["answer"])
    demo_val = m.group(1) if m else "?"
    if not skip_asset:
        import shutil
        shutil.copyfile(os.path.join(HERE, "sky_tool.py"), os.path.join(teai, "web", "blog-assets", "sky_tool.py"))
    tool_line = demo["tool_log"].splitlines()[0].replace("[ツール] ", "")
    prompt_tpl = (
        "次の条文だけを根拠に答えてください。\n"
        "【条文】（e-Gov法令検索からコピーした条文を貼る）\n"
        "【質問】（例：第一種住居地域で前面道路が6mのとき、容積率の上限を決める条項と、幅員に掛ける数値は？）\n"
        "答え方：1行目に答えの数値、2行目に根拠の条番号、3行目に計算が要る場合は式を1行。\n"
        "条文に書いていないことは「条文に記載なし」と書く。式の結果は自分で検算する。"
    )
    yen = luna_t["cost_per_task_usd"] * 150
    tw, tc, tlo, thi = two_run_stats()
    ms = manual_stats()
    g55, lun = ms["openai/gpt-5.5"], ms["openai/gpt-5.6-luna"]
    ex = next((r for r in lun["wrong"]), None)
    ex_line = ""
    if ex:
        import re as _re2
        m_ = _re2.search(r"min\([^\n]*", ex.get("pred", ""))
        ex_line = esc(m_.group(0)) if m_ else ""
    tldr = (f'<li data-ja="明日から：ChatGPTに法規を聞くときは条文を貼り、「数値と条番号だけ」で答えさせる（API経由のGPT-5.5で計算問題{g55["base"][0]}/{g55["base"][1]} → {g55["manual"][0]}/{g55["manual"][1]}。ChatGPTアプリでは未測定）。" '
            f'data-en="From tomorrow: when you ask ChatGPT about the code, paste the article and ask for only the value and article number (GPT-5.5 via API on calculation tasks: {g55["base"][0]}/{g55["base"][1]} → {g55["manual"][0]}/{g55["manual"][1]}; the ChatGPT app itself was not measured).">'
            f'明日から：ChatGPTに法規を聞くときは条文を貼り、「数値と条番号だけ」で答えさせる（API経由のGPT-5.5で計算問題{g55["base"][0]}/{g55["base"][1]} → {g55["manual"][0]}/{g55["manual"][1]}。ChatGPTアプリでは未測定）。</li>\n'
            f'<li data-ja="天空率はAIに手計算させない。手計算は最良でも{pct(luna_r["accuracy"], 0)}、計算ツールを渡すと{pct(luna_t["accuracy"], 0)}。" '
            f'data-en="Never let AI compute the sky factor by hand: at best {pct(luna_r["accuracy"], 0)} by hand, {pct(luna_t["accuracy"], 0)} with a calculation tool.">'
            f'天空率はAIに手計算させない。手計算は最良でも{pct(luna_r["accuracy"], 0)}、計算ツールを渡すと{pct(luna_t["accuracy"], 0)}。</li>\n'
            f'<li data-ja="大事な数字は、新しいチャットでもう1回聞く。2回測った安価な2モデルでは、誤答の9割超（{tlo}〜{thi}%）が2回目と食い違い、気づけた（上位モデルでは未測定）。" '
            f'data-en="For figures that matter, ask again in a fresh chat: for the two low-cost models we ran twice, over 90% ({tlo}–{thi}%) of wrong answers disagreed with the second run and could be caught (not measured on top models).">'
            f'大事な数字は、新しいチャットでもう1回聞く。2回測った安価な2モデルでは、誤答の9割超（{tlo}〜{thi}%）が2回目と食い違い、気づけた（上位モデルでは未測定）。</li>')
    sec = f"""<section id="use">
<h2 data-ja="この記事の使い方：立場別に、今日からできること" data-en="How to use this: what you can do today, by role">この記事の使い方：立場別に、今日からできること</h2>
<p class="lead" data-ja="結論は一つです。建築の数字をAIに出させるときは、条文は渡し、計算は道具にさせる。AIの仕事は、条文を読んで、道具に正しい値を渡すことです。" data-en="One conclusion: when AI produces building-code numbers, supply the articles and let a tool do the arithmetic. The AI's job is to read the articles and hand the right values to the tool.">結論は一つです。建築の数字をAIに出させるときは、条文は渡し、計算は道具にさせる。AIの仕事は、条文を読んで、道具に正しい値を渡すことです。</p>
<p class="tomorrow" data-ja="明日やることは1つ。ChatGPTに法規を聞くときは、e-Govの条文を一緒に貼り、「数値と条番号だけ」で答えさせる。" data-en="One thing to do tomorrow: when you ask ChatGPT about the code, paste the e-Gov article with it and ask for only the value and the article number.">明日やることは1つ。ChatGPTに法規を聞くときは、e-Govの条文を一緒に貼り、「数値と条番号だけ」で答えさせる。</p>
<p class="note" data-ja="測ったのは、API経由のモデルによる条文の読み取りと、条文の式でできる計算（建蔽率・容積率・採光・天空率など）です。ChatGPTアプリの計算機能や、条例・告示・日影・避難を含む法規チェック全体は測っていません。アプリが計算した場合も、式を1行出させて寸法と式を自分で確かめてください。" data-en="We measured API models reading the articles and doing the arithmetic their formulas allow (coverage, FAR, daylight, sky factor). Chat apps’ own calculation features and full code review (ordinances, notices, shadow, egress) were not measured. Even when an app calculates, have it show the formula on one line and check dimensions and formula yourself.">測ったのは、API経由のモデルによる条文の読み取りと、条文の式でできる計算（建蔽率・容積率・採光・天空率など）です。ChatGPTアプリの計算機能や、条例・告示・日影・避難を含む法規チェック全体は測っていません。アプリが計算した場合も、式を1行出させて寸法と式を自分で確かめてください。</p>

<h3 data-ja="1. ChatGPTなどで法規を調べている（設計・審査の実務者）" data-en="1. You look up regulations with ChatGPT or similar (design and review practitioners)">1. ChatGPTなどで法規を調べている（設計・審査の実務者）</h3>
<p data-ja="上の1つだけでも効果があります。余裕があれば、次の4つまでやると確実です。" data-en="The one step above already helps. If you have time, these four make it reliable.">上の1つだけでも効果があります。余裕があれば、次の4つまでやると確実です。</p>
<ol>
<li data-ja="e-Gov法令検索で該当条文を開き、条文そのものを質問に貼る（203問では、これだけで全モデルの正解率が上がった）。" data-en="Open the article on e-Gov and paste its text into the question (on the 203 tasks, this alone raised every model).">e-Gov法令検索で該当条文を開き、条文そのものを質問に貼る（203問では、これだけで全モデルの正解率が上がった）。</li>
<li data-ja="答えは「数値」と「条番号」だけで返させる。長い説明は検算できない。" data-en="Ask for only the value and the article number. Long explanations can't be checked.">答えは「数値」と「条番号」だけで返させる。長い説明は検算できない。</li>
<li data-ja="面積・天空率などの計算はAIにさせず、計算ソフトか下のツールで出す。" data-en="Don't let the AI do area or sky-factor arithmetic; use calculation software or the tool below.">面積・天空率などの計算はAIにさせず、計算ソフトか下のツールで出す。</li>
<li data-ja="最後は人が条文で確かめる。手早く当たりを付けるなら、新しいチャットで同じ質問をもう1回し、答えが違えば必ず条文に戻る。同じ条件で2回測った安価な2モデル（Qwen3.5 Flash・GPT-5.4 Nano）では、1回目の誤答{tw}件のうち{tc}件（{tlo}〜{thi}%）で2回目の答えが食い違い、この確認で気づけました。上位モデルが同じ誤答を2回返す場合には効かないので、その場合は条文で確かめるのが前提です。" data-en="A person checks the article in the end. For a quick screen, ask again in a fresh chat and go back to the article whenever the answers differ. For the two low-cost models we ran twice (Qwen3.5 Flash, GPT-5.4 Nano), {tc} of {tw} first-run errors ({tlo}–{thi}%) came back with a different second answer, so this check would have caught them.">最後は人が条文で確かめる。手早く当たりを付けるなら、新しいチャットで同じ質問をもう1回し、答えが違えば必ず条文に戻る。同じ条件で2回測った安価な2モデル（Qwen3.5 Flash・GPT-5.4 Nano）では、1回目の誤答{tw}件のうち{tc}件（{tlo}〜{thi}%）で2回目の答えが食い違い、この確認で気づけました。上位モデルが同じ誤答を2回返す場合には効かないので、その場合は条文で確かめるのが前提です。</li>
</ol>
<p class="note" data-ja="この手順そのままでも測りました。下の「貼る条文の目安」の条文を丸ごと貼り、質問の型で聞くと、計算問題{g55['manual'][1]}問でGPT-5.5は条文なし{g55['base'][0]}問正解 → 貼ると{g55['manual'][0]}問、GPT-5.6 Lunaは{lun['base'][0]}問 → {lun['manual'][0]}問でした。" data-en="We also measured this exact procedure: pasting the articles from “which articles to paste” below in full and using the template, on {g55['manual'][1]} calculation tasks GPT-5.5 went from {g55['base'][0]} correct without articles to {g55['manual'][0]}, and GPT-5.6 Luna from {lun['base'][0]} to {lun['manual'][0]}.">この手順そのままでも測りました。下の「貼る条文の目安」の条文を丸ごと貼り、質問の型で聞くと、計算問題{g55['manual'][1]}問でGPT-5.5は条文なし{g55['base'][0]}問正解 → 貼ると{g55['manual'][0]}問、GPT-5.6 Lunaは{lun['base'][0]}問 → {lun['manual'][0]}問でした。</p>
<p data-ja="Lunaが外した1問は、3行目の式が「{ex_line}」となっていました。小さい方を選ぶ式で大きい方を答えています。式を1行書かせておけば、こうした誤りは人が一目で見つけられます。" data-en="The one task Luna missed showed its third line as “{ex_line}”: a min() that returned the larger value. With the formula on one line, a person spots this at a glance.">Lunaが外した1問は、3行目の式が「{ex_line}」となっていました。小さい方を選ぶ式で大きい方を答えています。式を1行書かせておけば、こうした誤りは人が一目で見つけられます。</p>
<p data-ja="貼る条文の目安（今回の問題で使ったもの）：建蔽率は法第53条、容積率は法第52条第1項・第2項、採光補正係数は令第20条、天空率は令第135条の5〜第135条の9（前面道路が2本なら令第132条も。天空率の値そのものではなく、測定点の位置や数を確かめる用）。条例・告示・特定行政庁の取り扱いは別に確かめてください。" data-en="Which articles to paste (as used in these tasks): coverage – Act Art. 53; FAR – Act Art. 52(1)(2); daylight factor – Order Art. 20; sky factor – Order Arts. 135-5 to 135-9 (plus Art. 132 for two fronting roads; for checking measurement points, not for the value itself). Check ordinances, notices and local authority practice separately.">貼る条文の目安（今回の問題で使ったもの）：建蔽率は法第53条、容積率は法第52条第1項・第2項、採光補正係数は令第20条、天空率は令第135条の5〜第135条の9（前面道路が2本なら令第132条も。天空率の値そのものではなく、測定点の位置や数を確かめる用）。条例・告示・特定行政庁の取り扱いは別に確かめてください。</p>
<p class="links"><a href="https://laws.e-gov.go.jp/law/325AC0000000201#Mp-Ch_3-Se_4-At_52" data-ja="法第52条（容積率）→" data-en="Act Art. 52 (FAR) →">法第52条（容積率）→</a><a href="https://laws.e-gov.go.jp/law/325AC0000000201#Mp-Ch_3-Se_4-At_53" data-ja="法第53条（建蔽率）→" data-en="Act Art. 53 (coverage) →">法第53条（建蔽率）→</a><a href="https://laws.e-gov.go.jp/law/325CO0000000338#Mp-Ch_2-Se_1-At_20" data-ja="令第20条（採光補正係数）→" data-en="Order Art. 20 (daylight) →">令第20条（採光補正係数）→</a><a href="https://laws.e-gov.go.jp/law/325CO0000000338#Mp-Ch_7-At_135_9" data-ja="令第135条の9（天空率の測定点）→" data-en="Order Art. 135-9 (measurement points) →">令第135条の9（天空率の測定点）→</a></p>
<p data-ja="貼るのは、聞きたいことに関係する条1つ分で十分です（今回の手順では、容積率なら法第52条の全文、約6,200字を貼りました）。" data-en="One article relevant to your question is enough (for FAR we pasted the full Art. 52, about 6,200 characters).">貼るのは、聞きたいことに関係する条1つ分で十分です（今回の手順では、容積率なら法第52条の全文、約6,200字を貼りました）。</p>
<p data-ja="そのまま貼れる質問の型：" data-en="A question template you can paste (in Japanese):">そのまま貼れる質問の型：</p>
<pre><code>{esc(prompt_tpl)}</code></pre>

<h3 data-ja="2. 実際の土地で、天空率や建てられる大きさを知りたい（設計・不動産）" data-en="2. You want the sky factor or buildable size for a real lot (design, real estate)">2. 実際の土地で、天空率や建てられる大きさを知りたい（設計・不動産）</h3>
<p data-ja="天空率はAIのチャットに手計算させないでください。今回、手計算では最良のモデルでも約半分しか合いませんでした。お手持ちの天空率ソフトがあれば、それが最優先です。下の診断は確認申請に使える精度のものではなく、土地を検討する初期の当たり付け用です。地図で土地を選ぶと用途地域と前面道路から上限と道路斜線どおりの規模を無料で出し、天空率の検討をレポート（3,300円）で出す道具も使えます。この道具の天空率計算は今回の正解計算とは別の作り方で、140通りの形状で0.17ポイント以内の一致を確かめています。" data-en="Don't have a chat AI compute the sky factor by hand; even the best model here got about half right. If you have sky-factor software, use it first. The check below is not permit-grade; it is for early screening of a lot. It takes a lot picked on a map, derives the limits and the road-setback volume for free from the zoning and fronting road, and produces a sky-factor study as a report (JPY 3,300). Its sky-factor engine is built independently of this study's ground truth and agrees within 0.17 points across 140 shapes.">天空率はAIのチャットに手計算させないでください。今回、手計算では最良のモデルでも約半分しか合いませんでした。お手持ちの天空率ソフトがあれば、それが最優先です。下の診断は確認申請に使える精度のものではなく、土地を検討する初期の当たり付け用です。地図で土地を選ぶと用途地域と前面道路から上限と道路斜線どおりの規模を無料で出し、天空率の検討をレポート（3,300円）で出す道具も使えます。この道具の天空率計算は今回の正解計算とは別の作り方で、140通りの形状で0.17ポイント以内の一致を確かめています。</p>
<p data-ja="天空率で明日からAIに任せてよいのは、測定点の数と間隔を出させる作業です。令第135条の9を一緒に渡して聞くと（今回は自動で渡した条件）、手計算でも80〜100%合いました。ソフトに入れる測定点の設定と照らし合わせる用途に使えます。" data-en="The sky-factor task you can hand to AI from tomorrow is working out the number and spacing of measurement points: with Art. 135-9 supplied (automatically, in our setup), even by hand it was 80–100% correct. Use it to cross-check the points you enter in your software.">天空率で明日からAIに任せてよいのは、測定点の数と間隔を出させる作業です。令第135条の9を一緒に渡して聞くと（今回は自動で渡した条件）、手計算でも80〜100%合いました。ソフトに入れる測定点の設定と照らし合わせる用途に使えます。</p>
<p class="note" data-ja="bim.house は、この記事を書いた teai と同じイネブラ（Enabler Inc.）が運営しています。" data-en="bim.house is run by Enabler Inc., the same company behind teai, which wrote this article.">bim.house は、この記事を書いた teai と同じイネブラ（Enabler Inc.）が運営しています。</p>
<p class="links"><a href="https://bim.house/tochi/tateru" data-ja="建てられる大きさ診断（bim.house）を開く →" data-en="Open the buildable-size check (bim.house) →">建てられる大きさ診断（bim.house）を開く →</a></p>

<h3 data-ja="3. 自社のシステムやAIに組み込みたい（開発者）" data-en="3. You want to build this into your own system or AI (developers)">3. 自社のシステムやAIに組み込みたい（開発者）</h3>
<p data-ja="今回の計算ツールを1ファイルで公開しました（Python、外部ライブラリなし、MIT）。計算だけならAPIキーは不要です。AIに質問させる場合は、teai に無料登録してAPIキーを発行し、使う分をチャージします（月額なし）。" data-en="We've published the calculation tool as a single file (Python, no dependencies, MIT). No API key is needed for calculation alone; to let an AI answer questions with it, sign up free at teai, issue an API key and top up what you use (no monthly fee).">今回の計算ツールを1ファイルで公開しました（Python、外部ライブラリなし、MIT）。計算だけならAPIキーは不要です。AIに質問させる場合は、teai に無料登録してAPIキーを発行し、使う分をチャージします（月額なし）。</p>
<details class="cli"><summary data-ja="コマンドの使い方と座標の決め方を開く" data-en="Show commands and coordinates">コマンドの使い方と座標の決め方を開く</summary>
<p data-ja="座標の決め方：前面道路に沿う方向を x、道路から敷地の奥へ向かう方向を y とし、前面道路は y=-幅員〜0、敷地は y≥0 に置きます。--point は測定点の「x,y」、--box は建物の「x の始まり,x の終わり,y の始まり,y の終わり,高さ」です（単位m、高さは路面の中心から）。" data-en="Coordinates: x runs along the front road, y runs from the road into the lot; the road occupies y = −width…0 and the lot y ≥ 0. --point is the measurement point “x,y”; --box is a building “x start, x end, y start, y end, height” (metres; height from the road centre).">座標の決め方：前面道路に沿う方向を x、道路から敷地の奥へ向かう方向を y とし、前面道路は y=-幅員〜0、敷地は y≥0 に置きます。--point は測定点の「x,y」、--box は建物の「x の始まり,x の終わり,y の始まり,y の終わり,高さ」です（単位m、高さは路面の中心から）。</p>
<pre><code>  敷地 (y≥0)            ┌──────────┐ 建物 --box 0,10,2,14,12
                        │          │
  ──────────────────────┴──────────┴── y=0  道路境界
  前面道路（幅員6m）
  ●─────────────────────────────────── y=-6 反対側の境界線
  測定点 --point 0,-6</code></pre>
<pre><code># 天空率を計算する（AIなし）
python3 sky_tool.py calc --point 0,-6 --box 0,10,2,14,12

# 測定点の数（間口12m・幅員6m）。--json を付けると各点の座標も出る
python3 sky_tool.py points --frontage 12 --road 6

# 計画建築物と道路高さ制限適合建築物を、測定点ごとに比べる（--plan / --conf は何個でも）
python3 sky_tool.py compare --frontage 12 --road 6 --plan 1,11,2,14,15 --conf 0,12,0,6,7.5 --conf 0,12,6,12,15

# 適合建築物を自動で作って比べる（勾配・適用距離・後退距離から段状に近似）
python3 sky_tool.py compare --frontage 12 --road 6 --depth 15 --slope 1.25 --apply 20 --setback 1 --plan 1,11,2,14,15

# AIに質問し、計算はツールにさせる
export TEAI_API_KEY=...
python3 sky_tool.py ask "{esc(demo['question'])}"</code></pre>
<p data-ja="compare は、測定点ごとに計画建築物と適合建築物の天空率を並べ、最も不利な点で判定します。--depth を付けると、適合建築物を勾配・適用距離・後退距離から自動で作ります（敷地は長方形・前面道路1本・地盤は平坦の場合）（出力例の最後の行：「判定: 不適合（最も不利な点 P1、差 -1.159ポイント）」）。最後のコマンドを実際に動かした記録です。AIは質問から座標を読み取ってツールを1回呼び、ツールの値をそのまま答えました。" data-en="compare lists both sky factors per point and judges on the worst point; with --depth it builds the conforming building automatically from slope, application distance and setback (rectangular lot, one fronting road, flat ground) (example last line: “判定: 不適合（最も不利な点 P1、差 -1.159ポイント）”). Here is a real run of the last command. The AI read the coordinates from the question, called the tool once, and answered with the tool's value.">compare は、測定点ごとに計画建築物と適合建築物の天空率を並べ、最も不利な点で判定します。--depth を付けると、適合建築物を勾配・適用距離・後退距離から自動で作ります（敷地は長方形・前面道路1本・地盤は平坦の場合）（出力例の最後の行：「判定: 不適合（最も不利な点 P1、差 -1.159ポイント）」）。最後のコマンドを実際に動かした記録です。AIは質問から座標を読み取ってツールを1回呼び、ツールの値をそのまま答えました。</p>
<pre><code>AIが呼んだツール：{esc(tool_line)}
AIの答え：天空率 {esc(demo_val)}%（根拠：建築基準法施行令第135条の5）</code></pre>
</details>
<p class="links"><a href="/blog-assets/sky_tool.py" download data-ja="sky_tool.py をダウンロード →" data-en="Download sky_tool.py →">sky_tool.py をダウンロード →</a><a href="/docs" data-ja="APIキーの発行と使い方 →" data-en="Getting an API key →">APIキーの発行と使い方 →</a></p>
<p class="note" data-ja="このツールが扱うのは直方体の組み合わせまでです（斜線どおりの斜面は段状の直方体で近似します。上の compare の例は、後退距離による緩和を考慮していない単純な例です）。確認申請に使う数値は、申請に用いる天空率ソフトで必ず確認してください。最終的な適合の判断は、資格のある設計者が行います。" data-en="The tool handles combinations of boxes only. Confirm any figure for a building-permit application with the sky-factor software used for the application. The final compliance judgment rests with a qualified designer.">このツールが扱うのは直方体の組み合わせまでです（斜線どおりの斜面は段状の直方体で近似します。上の compare の例は、後退距離による緩和を考慮していない単純な例です）。確認申請に使う数値は、申請に用いる天空率ソフトで必ず確認してください。最終的な適合の判断は、資格のある設計者が行います。</p>
</section>
"""
    return sec, tldr


SKY2_KIND = {"calc_sky_grade": ("地盤が道路より高い敷地の天空率", "Sky factor, lot above road"),
             "sky_points_2road": ("角地（前面道路2本）の測定点", "Corner lot measurement points")}


def build_sky2() -> str:
    """地盤の高低差・角地の20問（results/sky2-rag・sky2-tools）。結果がそろっていなければ空。"""
    try:
        rag, tools = summary("sky2-rag"), summary("sky2-tools")
    except FileNotFoundError:
        return ""
    models = [(m, l) for m, l in SKY_ORDER if m in rag and m in tools]
    if not models:
        return ""
    n = sum(1 for _ in open(os.path.join(ROOT, "data", "tasks_sky2.jsonl"), encoding="utf-8"))

    def cell(sm, m, k):
        r = sm[m]
        if r["errors"] > r["n"] * 0.1:
            return "—"
        return pct(r["by_kind"][k]["accuracy"], 0)

    heads = "".join(f'<th scope="col" class="num">{l}</th>' for _, l in models)
    rows = []
    for k, (ja, en) in SKY2_KIND.items():
        cells = "".join(f'<td class="num">{cell(rag, m, k)} → {cell(tools, m, k)}</td>' for m, _ in models)
        rows.append(f'<tr><td data-ja="{ja}（10問）" data-en="{en} (10)">{ja}（10問）</td>{cells}</tr>')
    body = "\n".join(rows)
    lo_t2 = pct(min(tools[m]["accuracy"] for m, _ in models), 0)
    hi_t2 = pct(max(tools[m]["accuracy"] for m, _ in models), 0)
    return f"""<h3 data-ja="続き：地盤の高低差と、角地（{n}問）" data-en="Continued: raised lots and corner lots ({n} tasks)">続き：地盤の高低差と、角地（{n}問）</h3>
<p data-ja="実務で迷いやすい2つを追加しました。敷地が道路より高いと、測定点は（高低差−1m）の半分だけ高い位置とみなし（令135条の9第4項）、敷地の地盤そのものも空を遮る側に数えます（令135条の5）。角地では、狭い道路側のうち広い道路から幅員の2倍かつ35m以内は広い道路と同じ幅員とみなし（令132条）、その区域ごとに測定点を置きます（令135条の9第3項）。" data-en="Two cases practitioners often stumble on. When the lot sits above the road, the measurement point is deemed raised by half of (difference − 1 m) (Art. 135-9(4)) and the lot's own ground counts as obstruction (Art. 135-5). On a corner lot, the narrower road within twice the wider road's width and 35 m of it is deemed as wide as the wider road (Art. 132), and points are placed per zone (Art. 135-9(3)).">実務で迷いやすい2つを追加しました。敷地が道路より高いと、測定点は（高低差−1m）の半分だけ高い位置とみなし（令135条の9第4項）、敷地の地盤そのものも空を遮る側に数えます（令135条の5）。角地では、狭い道路側のうち広い道路から幅員の2倍かつ35m以内は広い道路と同じ幅員とみなし（令132条）、その区域ごとに測定点を置きます（令135条の9第3項）。</p>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="正解率（手計算 → 計算ツールあり）" data-en="Accuracy (by hand → with tool)">正解率（手計算 → 計算ツールあり）</caption>
<thead><tr><th scope="col" data-ja="種別" data-en="Type">種別</th>{heads}</tr></thead>
<tbody>
{body}
</tbody></table></div>
<p class="note" data-ja="「—」は、teai から各モデルの提供元へつなぐ経路が一時的に止まり、手計算の応答が1割以上返らなかったため測れなかったものです。経路の復旧後、同じモデルが応答することは確かめています。" data-en="“—” means the upstream route was temporarily unavailable and over 10% of hand-calculation requests returned nothing, so it was not scored.">「—」は、teai から各モデルの提供元へつなぐ経路が一時的に止まり、手計算の応答が1割以上返らなかったため測れなかったものです。経路の復旧後、同じモデルが応答することは確かめています。</p>
<p data-ja="計算ツールありの正解率は、モデルごとの20問全体で{lo_t2}〜{hi_t2}（種別ごとでは70〜100%）で、単純な形の50問（98〜100%）より下がりました。" data-en="With the tool, per-model accuracy over these 20 was {lo_t2}–{hi_t2} (70–100% by type), below the 98–100% on the 50 simpler tasks.">計算ツールありの正解率は、モデルごとの20問全体で{lo_t2}〜{hi_t2}（種別ごとでは70〜100%）で、単純な形の50問（98〜100%）より下がりました。</p>
<p data-ja="ツールを渡しても、高低差の問題では「敷地の地盤も空を遮る」ことを入れ忘れて外す回答がありました。ツールは正しく計算しますが、何を入力するかを決めるのは条文を読む側です。角地の問題では、問題文の「中心線から10mを超える区域」の書き方で迷った回答が1件あり、これは問題文の書き方にも原因があります。" data-en="Even with the tool, some raised-lot answers missed that the lot's own ground obstructs the sky. The tool computes correctly; deciding what to feed it is still a matter of reading the articles. One corner-lot answer was confused by our wording about the area beyond 10 m from the centreline, which is partly a flaw in the question.">ツールを渡しても、高低差の問題では「敷地の地盤も空を遮る」ことを入れ忘れて外す回答がありました。ツールは正しく計算しますが、何を入力するかを決めるのは条文を読む側です。角地の問題では、問題文の「中心線から10mを超える区域」の書き方で迷った回答が1件あり、これは問題文の書き方にも原因があります。</p>"""


def build_sky(teai: str, skip_asset: bool) -> tuple[str, str]:
    """天空率50問（results/sky-rag・sky-tools）の節。数字はすべて保存済みの結果から計算する。"""
    sys.path.insert(0, ROOT)
    from bench.sky import sky_ratio
    tasks = [json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks_sky.jsonl"), encoding="utf-8")]
    n = len(tasks)
    rag, tools = summary("sky-rag"), summary("sky-tools")

    # 上流の通信エラーが1割を超えたモデルの手計算は「測定できず」とし、範囲・内訳から外す
    unmeasured = {m for m, _ in SKY_ORDER if rag[m]["errors"] > n * 0.1}
    um_labels = "・".join(label for m, label in SKY_ORDER if m in unmeasured)
    um_ja = (um_labels + "の手計算は、上流の通信エラーで半数以上の問題に応答が返らなかったため、正解率を出していません（種別の表は、応答があった問題だけの正解数）。") if um_labels else ""
    um_en = (" " + um_labels + " by hand is not scored: upstream errors left most requests without a response (the per-type table counts only answered tasks).") if um_labels else ""

    # 手計算の失敗の内訳（出力上限で途中終了／「不明」／答えたが誤り／通信エラー）
    fail = {"length": 0, "unknown": 0, "wrong": 0, "error": 0}
    for m, _ in SKY_ORDER:
        if m in unmeasured:
            continue
        for r in load("sky-rag", m).values():
            if r.get("correct"):
                continue
            if r.get("error"):
                fail["error"] += 1
            elif r.get("finish") == "length":
                fail["length"] += 1
            elif "不明" in (r.get("pred") or "")[-80:]:
                fail["unknown"] += 1
            else:
                fail["wrong"] += 1

    # bim.house の天空率エンジン（Rust・本番と同じ160分割）との突き合わせ
    rust = {}
    for line in open(os.path.join(ROOT, "results", "sky-crosscheck", "rust_tateru_engine.tsv"), encoding="utf-8"):
        k, v160, v1200 = line.rstrip("\n").split("\t")
        rust[k] = (float(v160), float(v1200))
    geo = {}
    for t in tasks:
        g = t.get("geometry")
        if not g:
            continue
        if t["kind"] == "calc_sky":
            geo[t["id"]] = (g["point"], g["boxes"])
        else:
            for i, pt in enumerate(g["points"]):
                geo[f"{t['id']}-P{i+1}-plan"] = (pt, g["plan"])
                geo[f"{t['id']}-P{i+1}-conf"] = (pt, g["conforming"])
    ours = {k: sky_ratio(p[0], p[1], b) * 100 for k, (p, b) in geo.items()}
    d160 = max(abs(ours[k] - rust[k][0]) for k in geo)
    d1200 = max(abs(ours[k] - rust[k][1]) for k in geo)
    agree = cmp_n = 0
    for t in tasks:
        if t["kind"] != "sky_compare":
            continue
        cmp_n += 1
        diffs = [rust[f"{t['id']}-P{i}-plan"][0] - rust[f"{t['id']}-P{i}-conf"][0] for i in (1, 2, 3)]
        ok = all(d >= 0 for d in diffs)
        imin = min(range(3), key=lambda i: diffs[i]) + 1
        want_ok, want_p = t["answer_norm"].split(":")[1].split("#")
        agree += (str(int(ok)) == want_ok and str(imin) == want_p)

    def hand_cell(m: str) -> str:
        if m in unmeasured:
            return (f'<td class="num" data-ja="測定できず（{rag[m]["errors"]}/{n}問が上流エラー）" '
                    f'data-en="not measured ({rag[m]["errors"]}/{n} upstream errors)">'
                    f'測定できず（{rag[m]["errors"]}/{n}問が上流エラー）</td>')
        return f'<td class="num">{pct(rag[m]["accuracy"])}</td>'

    def kind_cell(m: str, k: str) -> str:
        if m in unmeasured:
            rows_k = [r for r in load("sky-rag", m).values() if r["kind"] == k and not r.get("error")]
            hand = f'{sum(1 for r in rows_k if r["correct"])}/{len(rows_k)}' if rows_k else "—"
        else:
            hand = pct(rag[m]["by_kind"][k]["accuracy"], 0)
        return f'<td class="num">{hand} → {pct(tools[m]["by_kind"][k]["accuracy"], 0)}</td>'

    rows = []
    for m, label in SKY_ORDER:
        r, t = rag[m], tools[m]
        rows.append(
            f'<tr><td>{label}<br><span class="id">{m}</span></td>'
            f'{hand_cell(m)}'
            f'<td class="num"><strong>{pct(t["accuracy"])}</strong></td>'
            f'<td class="num">${t["cost_per_task_usd"]:.4f}</td>'
            f'<td class="num">{t["latency_median_s"]:.1f}s</td></tr>')
    kind_rows = []
    for k, (ja, en) in SKY_KIND.items():
        cells = "".join(kind_cell(m, k) for m, _ in SKY_ORDER)
        cnt = sum(1 for x in tasks if x["kind"] == k)
        kind_rows.append(f'<tr><td data-ja="{ja}（{cnt}問）" data-en="{en} ({cnt})">{ja}（{cnt}問）</td>{cells}</tr>')
    lo_r = min(rag[m]["accuracy"] for m, _ in SKY_ORDER if m not in unmeasured)
    hi_r = max(rag[m]["accuracy"] for m, _ in SKY_ORDER if m not in unmeasured)
    lo_t = min(tools[m]["accuracy"] for m, _ in SKY_ORDER)
    hi_t = max(tools[m]["accuracy"] for m, _ in SKY_ORDER)
    cost = sum(rag[m]["cost_total_usd"] + tools[m]["cost_total_usd"] for m, _ in SKY_ORDER)

    if not skip_asset:
        out = {"title": "kenchiku-bench 天空率50問", "measured": "2026-11-03",
               "tasks": tasks,
               "runs": {tag: {m: list(load(tag, m).values()) for m, _ in SKY_ORDER} for tag in ("sky-rag", "sky-tools")},
               "summary": {"sky-rag": rag, "sky-tools": tools},
               "tool_definitions": __import__("bench.run", fromlist=["TOOLS"]).TOOLS,
               "crosscheck": {"engine": "bim.house tateru_engine.rs", "max_abs_diff_pt_res160": round(d160, 4),
                              "max_abs_diff_pt_res1200": round(d1200, 4), "compare_agree": f"{agree}/{cmp_n}",
                              "cases": len(geo)},
               "followup_20": ({"tasks": [json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks_sky2.jsonl"), encoding="utf-8")],
                                "runs": {tag: {m: list(load(tag, m).values()) for m, _ in SKY_ORDER} for tag in ("sky2-rag", "sky2-tools")},
                                "summary": {"sky2-rag": summary("sky2-rag"), "sky2-tools": summary("sky2-tools")},
                                "note": "手計算でエラーが1割を超えたモデルは上流の遮断（HTTP 502/503）で測定できず、記事では — と表示"}
                               if os.path.exists(os.path.join(ROOT, "results", "sky2-rag", "summary.json")) else None),
               "commands": ["python3 bench/tasks_sky.py",
                            "python3 bench/run.py --models <id> --tasks data/tasks_sky.jsonl --rag --tag sky-rag",
                            "python3 bench/run.py --models <id> --tasks data/tasks_sky.jsonl --rag --tools --tag sky-tools"]}
        with open(os.path.join(teai, "web", "blog-assets", f"{SKY_ASSET}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)

    sky2_html = build_sky2()
    tldr = (f'<li data-ja="追試（11/3）：天空率{n}問は、条文を渡しても手計算では{pct(lo_r)}〜{pct(hi_r)}。計算をツールとして渡すと{pct(lo_t)}〜{pct(hi_t)}。" '
            f'data-en="Follow-up (Nov 3): on {n} sky-factor tasks, hand calculation with the articles scored {pct(lo_r)}–{pct(hi_r)}; given the calculation as a tool, {pct(lo_t)}–{pct(hi_t)}.">'
            f'追試（11/3）：天空率{n}問は、条文を渡しても手計算では{pct(lo_r)}〜{pct(hi_r)}。計算をツールとして渡すと{pct(lo_t)}〜{pct(hi_t)}。</li>')
    tbl = "\n".join(rows)
    krows = "\n".join(kind_rows)
    heads = "".join(f'<th scope="col" class="num">{label}</th>' for _, label in SKY_ORDER)
    sec = f"""<section id="sky">
<h2 data-ja="追試：天空率は、条文を渡しても解けない" data-en="Follow-up: the sky factor is not solved by supplying the articles">追試：天空率は、条文を渡しても解けない</h2>
<p data-ja="天空率（建築基準法施行令第135条の5〜9）の{n}問を11月3日に追加で測りました。天空率は、建物を想定半球に投影した面積から求めます。条文に書いてあるのは定義と測定点の決め方だけで、数値は立体の計算をしないと出ません。" data-en="On November 3 we added {n} sky-factor tasks (Enforcement Order Articles 135-5 to 135-9). The sky factor comes from the area of the building projected onto a hemisphere. The articles define it and say where to measure; the number itself requires a solid-geometry calculation.">天空率（建築基準法施行令第135条の5〜9）の{n}問を11月3日に追加で測りました。天空率は、建物を想定半球に投影した面積から求めます。条文に書いてあるのは定義と測定点の決め方だけで、数値は立体の計算をしないと出ません。</p>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="天空率{n}問：条文ありの手計算と、計算ツールを渡した場合" data-en="{n} sky-factor tasks: hand calculation with articles vs. with a calculation tool">天空率{n}問：条文ありの手計算と、計算ツールを渡した場合</caption>
<thead><tr><th scope="col" data-ja="モデル" data-en="Model">モデル</th><th scope="col" class="num" data-ja="手計算" data-en="By hand">手計算</th><th scope="col" class="num" data-ja="計算ツールあり" data-en="With tool">計算ツールあり</th><th scope="col" class="num" data-ja="1問あたり" data-en="Per task">1問あたり</th><th scope="col" class="num" data-ja="中央値" data-en="Median">中央値</th></tr></thead>
<tbody>
{tbl}
</tbody></table></div>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="種別ごとの正解率（手計算 → 計算ツールあり）" data-en="Accuracy by type (by hand → with tool)">種別ごとの正解率（手計算 → 計算ツールあり）</caption>
<thead><tr><th scope="col" data-ja="種別" data-en="Type">種別</th>{heads}</tr></thead>
<tbody>
{krows}
</tbody></table></div>
<p data-ja="測定点の数と間隔は条文の読み取りだけで決まるので、手計算でも多くのモデルが解けました。崩れたのは数値を出す2種です。{um_labels + "を除く" if um_labels else ""}手計算の失敗{sum(fail.values())}件の内訳は、出力の上限まで計算して答えに届かなかったもの{fail['length']}件、「不明」と答えたもの{fail['unknown']}件、答えたが数値や判定が違ったもの{fail['wrong']}件、通信エラー{fail['error']}件でした。{um_ja}" data-en="Measurement points follow from reading the article alone, so many models solved them by hand. The two numeric types broke down. Of {sum(fail.values())} hand-calculation failures{" (excluding " + um_labels + ")" if um_labels else ""}, {fail['length']} ran out of output before reaching an answer, {fail['unknown']} answered “unknown”, {fail['wrong']} gave a wrong value or verdict, and {fail['error']} were transport errors.{um_en}">測定点の数と間隔は条文の読み取りだけで決まるので、手計算でも多くのモデルが解けました。崩れたのは数値を出す2種です。{um_labels + "を除く" if um_labels else ""}手計算の失敗{sum(fail.values())}件の内訳は、出力の上限まで計算して答えに届かなかったもの{fail['length']}件、「不明」と答えたもの{fail['unknown']}件、答えたが数値や判定が違ったもの{fail['wrong']}件、通信エラー{fail['error']}件でした。{um_ja}</p>
<p data-ja="計算ツールは2つだけです。sky_ratio（測定点と直方体の一覧を受け取り天空率を返す）と sky_measurement_points（間口と幅員から測定点を返す）。モデルの仕事は、問題文から座標を読み取ってツールに渡し、結果を比べることに変わります。" data-en="There are only two tools: sky_ratio (takes a point and a list of boxes, returns the sky factor) and sky_measurement_points (returns the points from frontage and road width). The model's job becomes reading coordinates from the question, passing them to the tool and comparing the results.">計算ツールは2つだけです。sky_ratio（測定点と直方体の一覧を受け取り天空率を返す）と sky_measurement_points（間口と幅員から測定点を返す）。モデルの仕事は、問題文から座標を読み取ってツールに渡し、結果を比べることに変わります。</p>
<p class="note" data-ja="正解の計算は、方位角0.01°刻みの正射影積分です。無限に長い壁の解析解と小数第6位まで一致し、bim.house の天空率エンジン（別実装）とも{len(geo)}形状で最大{d160:.2f}ポイント差（エンジン側を細かくすると{d1200:.2f}ポイント差）、比較問題の判定は{agree}/{cmp_n}で一致しました。対象は平坦な敷地の直方体だけで、地盤の高低差・2以上の前面道路・隣地や北側の天空率は含みません。測定の費用は合計 ${cost:.2f} です。" data-en="Ground truth is an orthographic-projection integral at 0.01° azimuth steps. It matches the closed form for an infinite wall to six decimals, and agrees with bim.house's independent sky-factor engine within {d160:.2f} points across {len(geo)} shapes ({d1200:.2f} at higher engine resolution), with {agree}/{cmp_n} identical verdicts. Scope is boxes on flat sites only; grade differences, multiple fronting roads and side/north sky factors are excluded. Total measurement cost ${cost:.2f}.">正解の計算は、方位角0.01°刻みの正射影積分です。無限に長い壁の解析解と小数第6位まで一致し、bim.house の天空率エンジン（別実装）とも{len(geo)}形状で最大{d160:.2f}ポイント差（エンジン側を細かくすると{d1200:.2f}ポイント差）、比較問題の判定は{agree}/{cmp_n}で一致しました。対象は平坦な敷地の直方体だけで、地盤の高低差・2以上の前面道路・隣地や北側の天空率は含みません。測定の費用は合計 ${cost:.2f} です。</p>
{sky2_html}
<p class="links"><a href="/blog-assets/{SKY_ASSET}.json" data-ja="天空率50問の全応答・ツール定義・突き合わせ（JSON）→" data-en="All 50 sky-factor responses, tool definitions and cross-check (JSON) →">天空率50問の全応答・ツール定義・突き合わせ（JSON）→</a></p>
</section>
"""
    return sec, tldr


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

    sky_section, sky_tldr = build_sky(a.teai, a.skip_asset)
    st = strict_scores()
    strict_txt = "・".join(f"{label} {pct(st[m][0])}" for m, label in ORDER)
    strict_drop = max(st[m][1] - st[m][0] for m, _ in ORDER) * 100
    use_section, use_tldr = build_use(a.teai, a.skip_asset)

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
.note{border-left:3px solid var(--accent);padding:4px 0 4px 18px;color:#d9e5df;font-size:15.5px}
main p,main li{color:#e6efea}
details.cli{margin:12px 0}
details.cli>summary{cursor:pointer;color:var(--accent);padding:6px 0}
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
.toc{display:flex;flex-wrap:wrap;justify-content:flex-start;gap:8px 22px;padding:0 0 18px;font-size:14px;border-bottom:1px solid var(--line);margin-bottom:26px}
.toc a{text-decoration:none;border-bottom:1px solid transparent}
.toc a:hover,.toc a:focus-visible{border-bottom-color:var(--accent)}
.cta{border:1px solid #2f5a44;background:#0d1a15;border-radius:14px;padding:20px 24px;margin:26px 0 0}
.cta p{margin:6px 0}
.cta-links{display:flex;flex-wrap:wrap;align-items:center;gap:14px 22px;margin-top:14px}
.btn{display:inline-block;background:var(--accent);color:#04120b;font-weight:700;text-decoration:none;padding:10px 18px;border-radius:999px}
.btn:hover,.btn:focus-visible{filter:brightness(1.08)}
.muted-note{font-size:12px;color:var(--muted);display:block;margin-top:4px}
.cta-mid{border-left:3px solid var(--accent);background:#0d1a15;border-radius:0 10px 10px 0;padding:14px 18px;margin:24px 0 0}
.cta-mid p{margin:4px 0}
.cta-mid .lead{font-weight:700}
.cta-mid .links{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:8px;font-size:14px}
section>p.links{display:flex;flex-wrap:wrap;gap:8px 22px;margin:12px 0}
.related{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:10px 24px}
.related li{margin:0}
a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
details.devdetails{border:1px solid var(--line);border-radius:12px;padding:6px 18px;margin:30px 0}
details.devdetails>summary{cursor:pointer;font-weight:700;font-size:17px;padding:12px 0}
.tomorrow{font-weight:700;font-size:18px;border:1px solid #2f5a44;background:#0d1a15;border-radius:12px;padding:14px 18px;margin:18px 0}
h1{text-wrap:balance;word-break:keep-all;overflow-wrap:anywhere}
.sub{font-size:17px;color:var(--muted);margin:10px 0 22px;max-width:820px}
@media(max-width:600px){table.wide th:nth-child(n+6),table.wide td:nth-child(n+6){display:none}}
@media(max-width:600px){header{padding-top:30px}.lead{font-size:18px}th,td{padding:10px 8px}
.tldr ul{padding-left:18px}.tldr li{margin:6px 0}}
.tldr{border:1px solid var(--line);border-radius:12px;padding:16px 22px;margin:22px 0;background:#0c1519}
.tldr ul{margin:6px 0 0;padding-left:20px}
.tldr li{margin:8px 0;max-width:900px}
"""

    html = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>建築法規をAIに聞くなら、条文は貼り、計算は道具に。 — 建築基準法203問＋天空率の実測 | teai</title>
<meta name="description" content="建築基準法203問と天空率をAIで実測。天空率はAIの手計算だと最良でも52%、計算ツールを渡すと98%。設計・不動産・開発の立場別の使い方と、天空率の計算ツール（無料）を公開。" data-meta-ja="建築基準法203問と天空率をAIで実測。天空率はAIの手計算だと最良でも52%、計算ツールを渡すと98%。設計・不動産・開発の立場別の使い方と、天空率の計算ツール（無料）を公開。" data-meta-en="We measured AI on 203 Building Standard Law tasks and the sky factor: by hand, at best 52%; with a calculation tool, 98%. How to use it by role, plus a free sky-factor tool.">
<link rel="canonical" href="https://teai.io/blog/{SLUG}">
<link rel="alternate" hreflang="ja" href="https://teai.io/blog/{SLUG}">
<link rel="alternate" hreflang="en" href="https://teai.io/blog/{SLUG}?lang=en">
<meta property="og:title" content="建築法規をAIに聞くなら、条文は貼り、計算は道具に。 — 建築基準法203問＋天空率の実測 | teai" data-meta-ja="建築法規をAIに聞くなら、条文は貼り、計算は道具に。 — 建築基準法203問＋天空率の実測 | teai" data-meta-en="Asking AI about building codes: paste the articles, leave the arithmetic to a tool.">
<meta property="og:description" content="建築基準法203問と天空率をAIで実測。天空率はAIの手計算だと最良でも52%、計算ツールを渡すと98%。設計・不動産・開発の立場別の使い方と、天空率の計算ツール（無料）を公開。" data-meta-ja="建築基準法203問と天空率をAIで実測。天空率はAIの手計算だと最良でも52%、計算ツールを渡すと98%。設計・不動産・開発の立場別の使い方と、天空率の計算ツール（無料）を公開。" data-meta-en="We measured AI on 203 Building Standard Law tasks and the sky factor: by hand, at best 52%; with a calculation tool, 98%. How to use it by role, plus a free sky-factor tool.">
<meta property="og:type" content="article"><meta property="og:url" content="https://teai.io/blog/{SLUG}">
<meta property="og:image" content="https://teai.io/og-kenchiku-benchmark.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:image:alt" content="40回の応答のうち25回が推論ありで正解、15回が即答で不正解だったことを示す図" data-meta-ja="40回の応答のうち25回が推論ありで正解、15回が即答で不正解だったことを示す図" data-meta-en="A chart showing 25 of 40 responses reasoned and were correct, while 15 answered immediately and were wrong"><meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="建築法規をAIに聞くなら、条文は貼り、計算は道具に。">
<meta name="twitter:description" content="建築基準法203問と天空率をAIで実測。天空率はAIの手計算だと最良でも52%、計算ツールを渡すと98%。設計・不動産・開発の立場別の使い方と、天空率の計算ツール（無料）を公開。">
<meta name="twitter:image" content="https://teai.io/og-kenchiku-benchmark.png">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Article","headline":"建築法規をAIに聞くなら、条文は貼り、計算は道具に。","description":"建築基準法203問と天空率をAIで実測。天空率はAIの手計算だと最良でも52%、計算ツールを渡すと98%。設計・不動産・開発の立場別の使い方と、天空率の計算ツール（無料）を公開。","datePublished":"{DATE}","dateModified":"{UPDATED}","inLanguage":"ja","url":"https://teai.io/blog/{SLUG}","image":"https://teai.io/og-kenchiku-benchmark.png","author":{{"@type":"Organization","name":"teai.io","url":"https://teai.io"}},"publisher":{{"@type":"Organization","name":"teai.io","url":"https://teai.io"}}}}</script>
<style>{css}</style>
</head><body>
<a class="skip" href="#results" data-ja="本文へ移動" data-en="Skip to content">本文へ移動</a>
<nav><a class="brand" href="/">teai.</a><span><a href="/blog" id="blog-link" data-ja="実測記事一覧" data-en="Measured reports">実測記事一覧</a> · <a id="language" href="?lang=en">English</a></span></nav>
<main>
<header>
<div class="eyebrow">実測 · <time datetime="{DATE}">{DATE}</time>（追試 <time datetime="{UPDATED}">{UPDATED}</time>）· 建築基準法203問 ＋ 天空率50問（＋追加20問）</div>
<h1 data-ja="建築法規をAIに聞くなら、条文は貼り、計算は道具に。" data-en="Asking AI about building codes: paste the articles, leave the arithmetic to a tool.">建築法規をAIに聞くなら、条文は貼り、計算は道具に。</h1>
<p class="sub" data-ja="建築基準法203問と天空率50問で、AIを実測しました。法規を調べる人・土地を見る人・システムを作る人の立場別に、今日からの使い方を最初にまとめています。" data-en="We measured AI on 203 Building Standard Law tasks and 50 sky-factor tasks. How to use the results today, for people who look up rules, assess lots or build systems, comes first.">建築基準法203問と天空率50問で、AIを実測しました。法規を調べる人・土地を見る人・システムを作る人の立場別に、今日からの使い方を最初にまとめています。</p>
<nav class="toc" aria-label="目次" data-aria-en="Contents">
<a href="#use" data-ja="使い方" data-en="How to use this">使い方</a>
<a href="#results" data-ja="結果" data-en="Results">結果</a>
<a href="#sky" data-ja="天空率" data-en="Sky factor">天空率</a>
<a href="#details" data-ja="根拠の詳細（開発者向け）" data-en="Details for developers">根拠の詳細（開発者向け）</a>
<a href="#data" data-ja="記録データ" data-en="Evidence">記録データ</a>
</nav>
<div class="tldr">
<p><strong data-ja="要点" data-en="In short">要点</strong></p>
<ul>
{use_tldr}
</ul>
</div>
<p class="note" data-ja="この記事は、複数のAIモデルを1つのAPIで使えるサービス teai の開発者による実測で、独立した第三者の評価ではありません。正解は e-Gov 法令APIの条文と条文の式による計算だけから作り、採点にAIは使っていません。全応答と採点理由はJSONで公開しています（採点コードは非公開）。" data-en="This is a measurement by the developer of teai, a service that runs many AI models behind one API; it is not an independent evaluation. Answers come only from e-Gov law text and the articles’ formulas, with no AI grader. Every response and grading reason is published as JSON (grader code is private).">この記事は、複数のAIモデルを1つのAPIで使えるサービス teai の開発者による実測で、独立した第三者の評価ではありません。正解は e-Gov 法令APIの条文と条文の式による計算だけから作り、採点にAIは使っていません。全応答と採点理由はJSONで公開しています（採点コードは非公開）。</p>
</header>
{use_section}

<section id="results">
<p class="note" data-ja="次の表は、条文を渡すかどうかで正解率がどう変わったかです。設計者の方は表の下の「天空率」まで読めば十分です。" data-en="The table below shows how supplying the articles changed accuracy. Designers can stop after the sky-factor section that follows.">次の表は、条文を渡すかどうかで正解率がどう変わったかです。設計者の方は表の下の「天空率」まで読めば十分です。</p>
<h2 data-ja="結果：条文を渡すと、ほぼ別のモデルになる" data-en="Results: with the articles, these are almost different models">結果：条文を渡すと、ほぼ別のモデルになる</h2>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table class="wide">
<caption data-ja="6モデルの正解率・費用・待ち時間（条文あり）" data-en="Accuracy, cost and latency for six models (with articles)">6モデルの正解率・費用・待ち時間（条文あり）</caption>
<thead><tr><th scope="col" data-ja="モデル" data-en="Model">モデル</th><th scope="col" class="num" data-ja="条文なし" data-en="No articles">条文なし</th><th scope="col" class="num" data-ja="条文を渡す" data-en="With articles">条文を渡す</th><th scope="col" class="num" data-ja="1問あたり" data-en="Per task">1問あたり</th><th scope="col" class="num" data-ja="中央値" data-en="Median">中央値</th><th scope="col" class="num" data-ja="p90" data-en="p90">p90</th><th scope="col" class="num" data-ja="95%区間" data-en="95% CI">95%区間</th></tr></thead>
<tbody>
{body_rows}
</tbody></table></div>
<p class="note" data-ja="採点は「回答の中に正解の数値が含まれれば正解」のため、やや甘く出ます。回答の最後の答えだけで厳しく採点し直すと、条文ありの正解率は {strict_txt} でした（下げ幅は最大{strict_drop:.1f}ポイント）。結論は変わりません。" data-en="Grading counts a response as correct if the right value appears anywhere, so it is slightly generous. Re-graded strictly on the final answer only, accuracy with articles was {strict_txt} (at most {strict_drop:.1f} points lower). The conclusion holds.">採点は「回答の中に正解の数値が含まれれば正解」のため、やや甘く出ます。回答の最後の答えだけで厳しく採点し直すと、条文ありの正解率は {strict_txt} でした（下げ幅は最大{strict_drop:.1f}ポイント）。結論は変わりません。</p>
<figure class="bars">
<figcaption data-ja="条文なし → 条文ありの正解率（203問）" data-en="Accuracy without articles → with articles (203 tasks)">条文なし → 条文ありの正解率（203問）</figcaption>
{bar_rows}
</figure>
<p data-ja="中央値・p90は回答までの待ち時間（真ん中の値・遅い方から1割の値）、95%区間は問題数が限られることによる正解率の幅です（スマホでは右の2列を省略）。203問・temperature 0・各条件1回。安い順ではなく、今回測った6モデルをそのまま並べています。差が最も大きかったのは Gemini 2.5 Flash（+39.4ポイント）と GPT-5.4 Nano（+55.2ポイント）でした。" data-en="Median and p90 are response wait times (the middle value and the slowest-10% value); the 95% CI is the accuracy range implied by the limited number of tasks (the two right-hand columns are hidden on phones). 203 tasks, temperature 0, one run per condition. Ordered as measured, not by price. The largest gains were Gemini 2.5 Flash (+39.4 points) and GPT-5.4 Nano (+55.2 points).">中央値・p90は回答までの待ち時間（真ん中の値・遅い方から1割の値）、95%区間は問題数が限られることによる正解率の幅です（スマホでは右の2列を省略）。203問・temperature 0・各条件1回。安い順ではなく、今回測った6モデルをそのまま並べています。差が最も大きかったのは Gemini 2.5 Flash（+39.4ポイント）と GPT-5.4 Nano（+55.2ポイント）でした。</p>

<div class="cta-mid">
<p class="lead" data-ja="条文を渡すかどうかで、同じモデルの正解率が最大 +55.2 ポイント動きました。" data-en="Supplying the articles moved the same model's accuracy by up to +55.2 points.">条文を渡すかどうかで、同じモデルの正解率が最大 +55.2 ポイント動きました。</p>
<p data-ja="モデルを替える前に、渡し方を替える方が効く場合があります。teai は同じAPI・同じ料金体系で6モデルを切り替えられるので、自分の条文で同じ比較を回せます。" data-en="Before switching models, changing what you supply can matter more. teai runs all six models behind one API and one price list, so you can repeat this comparison on your own articles.">モデルを替える前に、渡し方を替える方が効く場合があります。teai は同じAPI・同じ料金体系で6モデルを切り替えられるので、自分の条文で同じ比較を回せます。</p>
<p class="links"><a href="/pricing" data-ja="料金とモデル一覧 →" data-en="Pricing and models →">料金とモデル一覧 →</a><a href="/blog-assets/{ASSET}.json" data-ja="この比較の生データ（JSON）→" data-en="Raw data for this comparison (JSON) →">この比較の生データ（JSON）→</a></p>
</div>
</section>

{sky_section}
<details id="details" class="devdetails">
<summary data-ja="開発者・検証向けの詳細（種別ごとの結果・40回の反復・費用・実例・条件と限界・導入時の判断）" data-en="Details for developers and reviewers (by type, 40 repeats, cost, examples, conditions, deployment decisions)">開発者・検証向けの詳細（種別ごとの結果・40回の反復・費用・実例・条件と限界・導入時の判断）</summary>
<section id="by-kind">
<h2 data-ja="種別ごと：採光と容積率で、モデル差が最も開く" data-en="By task type: daylight and FAR separate the models most">種別ごと：採光と容積率で、モデル差が最も開く</h2>
<p data-ja="条文を渡した状態の内訳です。数値穴埋めは6モデルすべて100%、誤り検出も96%以上でした。Qwen3.5 Flash を除く5モデルでは、7項目すべてが{rest_min:.0f}%以上に収まっています。開きが最も大きいのは Qwen の採光補正係数（{qwen_daylight:.0f}%）で、その行を外すと最大の差は応用計算の {rest_applied0:.0f}%〜{rest_applied1:.0f}% になります。応用計算は、前面道路の制限で容積率を決め、敷地面積を掛け、建蔽率側と並べて答える2段の計算です。" data-en="Breakdown with articles supplied. Numeric cloze was 100% for all six models and false-claim checks stayed above 96%. Excluding Qwen3.5 Flash, the other five stay at {rest_min:.0f}% or above on all seven types. The widest gap is Qwen's daylight score ({qwen_daylight:.0f}%); drop that row and the largest remaining spread is applied at {rest_applied0:.0f}–{rest_applied1:.0f}%. Applied means: derive the FAR cap from the front-road limit, multiply by site area, then answer alongside the BCR figure.">条文を渡した状態の内訳です。数値穴埋めは6モデルすべて100%、誤り検出も96%以上でした。Qwen3.5 Flash を除く5モデルでは、7項目すべてが{rest_min:.0f}%以上に収まっています。開きが最も大きいのは Qwen の採光補正係数（{qwen_daylight:.0f}%）で、その行を外すと最大の差は応用計算の {rest_applied0:.0f}%〜{rest_applied1:.0f}% になります。応用計算は、前面道路の制限で容積率を決め、敷地面積を掛け、建蔽率側と並べて答える2段の計算です。</p>
<div class="scroll" tabindex="0" role="region" aria-label="表（横にスクロールできます）" data-aria-en="Table (scrolls horizontally)"><table>
<caption data-ja="タスク種別ごとの正解率（条文あり・n は問題数）" data-en="Accuracy by task type (with articles; n = number of tasks)">タスク種別ごとの正解率（条文あり・n は問題数）</caption>
<thead><tr><th scope="col" data-ja="モデル" data-en="Model">モデル</th>{head}</tr></thead>
<tbody>
{kind_body}
</tbody></table></div>
<p class="note" data-ja="黄色は90%未満。42セル中{low_total}セルで、うち{low_qwen}セルが Qwen3.5 Flash の1行（採光 {qwen_daylight:.0f}%、容積率 {qwen_far:.0f}%、条文同定 {qwen_cite:.0f}%、建蔽率 {qwen_bcr:.0f}%、応用 {qwen_applied:.0f}%）。残りは {low_other_ja} です。Qwen は同じ条件の2回目で 91.6% まで戻っており、この低さが恒常的な能力なのか、下で扱う応答の振れの一部なのかは、この測定だけでは切り分けられません。" data-en="Amber marks under 90%: {low_total} of 42 cells, {low_qwen} of them in the Qwen3.5 Flash row (daylight {qwen_daylight:.0f}%, FAR {qwen_far:.0f}%, article lookup {qwen_cite:.0f}%, BCR {qwen_bcr:.0f}%, applied {qwen_applied:.0f}%). The rest are {low_other_en}. Qwen scored 91.6% on a second run of the same condition, so this data alone cannot say whether that row reflects stable capability or part of the response variance discussed below.">黄色は90%未満。42セル中{low_total}セルで、うち{low_qwen}セルが Qwen3.5 Flash の1行（採光 {qwen_daylight:.0f}%、容積率 {qwen_far:.0f}%、条文同定 {qwen_cite:.0f}%、建蔽率 {qwen_bcr:.0f}%、応用 {qwen_applied:.0f}%）。残りは {low_other_ja} です。Qwen は同じ条件の2回目で 91.6% まで戻っており、この低さが恒常的な能力なのか、下で扱う応答の振れの一部なのかは、この測定だけでは切り分けられません。</p>
</section>

<section id="repeat">
<h2 data-ja="40回の反復：同じ質問でも、答えの出し方が2種類あった" data-en="40 repeats: one question, two kinds of answer">40回の反復：同じ質問でも、答えの出し方が2種類あった</h2>
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

<div class="cta-mid">
<p class="lead" data-ja="同じ入力でも、返る応答の型が変わることがあります。" data-en="The same input can come back in a different shape.">同じ入力でも、返る応答の型が変わることがあります。</p>
<p data-ja="1回の測定でモデルを決めず、同じ条件で複数回まわしてから比べてください。この記事の203問と採点理由はJSONで公開しているので、そのまま自分の条件に当てられます。" data-en="Do not pick a model from a single run; repeat under identical conditions before comparing. The 203 tasks and their grading reasons are public in JSON, so you can apply them to your own conditions.">1回の測定でモデルを決めず、同じ条件で複数回まわしてから比べてください。この記事の203問と採点理由はJSONで公開しているので、そのまま自分の条件に当てられます。</p>
<p class="links"><a href="/blog-assets/{ASSET}.json" data-ja="203問と全応答を見る（JSON）→" data-en="See the 203 tasks and every response (JSON) →">203問と全応答を見る（JSON）→</a><a href="/docs" data-ja="APIドキュメント →" data-en="API docs →">APIドキュメント →</a></p>
</div>
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
<p data-ja="203問と採点理由はJSONでそのまま検証できます。採点コードは非公開のため、同じ採点で別モデルを比べたい場合は個別に共有します。" data-en="The 203 tasks and their grading reasons can be checked directly in the JSON. The grader is private; ask us if you want to compare another model under identical grading.">203問と採点理由はJSONでそのまま検証できます。採点コードは非公開のため、同じ採点で別モデルを比べたい場合は個別に共有します。</p>
<p class="cta-links"><a class="btn" href="/blog-assets/kenchiku-bench-20261101.json" data-ja="証跡JSONを開く" data-en="Open the evidence JSON">証跡JSONを開く</a>
<span class="muted-note" data-ja="採点コードは非公開リポジトリ（github.com/yukihamada/kenchiku-bench）にあり、必要な場合は個別に共有します" data-en="The grader lives in a private repository (github.com/yukihamada/kenchiku-bench); it can be shared on request">採点コードは非公開リポジトリ（github.com/yukihamada/kenchiku-bench）にあり、必要な場合は個別に共有します</span>
<a href="/docs" data-ja="APIドキュメント" data-en="API docs">APIドキュメント</a>
<a href="/pricing" data-ja="料金" data-en="Pricing">料金</a></p>
</div>
</section>

</details>
<section id="data">
<h2 data-ja="記録を確かめる" data-en="Inspect the evidence">記録を確かめる</h2>
<p><a href="/blog-assets/{ASSET}.json" data-ja="203問の全文・全応答・採点理由・反復プローブ・実行コマンド（JSON）" data-en="All 203 tasks, every response with grading reasons, the repeat probes and run commands (JSON)">203問の全文・全応答・採点理由・反復プローブ・実行コマンド（JSON）</a></p>
<p data-ja="測定コードとタスク生成コードは github.com/yukihamada/kenchiku-bench（非公開リポジトリ）にあり、公開している記録は上のJSONです。条文取得（corpus/fetch_laws.py）→タスク生成（bench/tasks.py）→自己テスト（bench/selftest.py）→測定（bench/run.py）の順で、測定しました（手順の記録としてJSONに収録）。コードは非公開なので、読者が確かめられるのはJSONの全応答と採点理由です。天空率の計算ツールだけは sky_tool.py として公開しています。" data-en="The measurement and task-generation code is at github.com/yukihamada/kenchiku-bench (a private repository); the public record is the JSON above. We ran, in order: fetch the law text (corpus/fetch_laws.py), generate tasks (bench/tasks.py), self-test (bench/selftest.py), measure (bench/run.py); the steps are recorded in the JSON. Because the code is private, what readers can verify is every response and grading reason in the JSON. Only the sky-factor tool is public, as sky_tool.py.">測定コードとタスク生成コードは github.com/yukihamada/kenchiku-bench（非公開リポジトリ）にあり、公開している記録は上のJSONです。条文取得（corpus/fetch_laws.py）→タスク生成（bench/tasks.py）→自己テスト（bench/selftest.py）→測定（bench/run.py）の順で、測定しました（手順の記録としてJSONに収録）。コードは非公開なので、読者が確かめられるのはJSONの全応答と採点理由です。天空率の計算ツールだけは sky_tool.py として公開しています。</p>
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
<footer><span data-ja="実測・執筆：teai.io · {DATE}（追試・改訂 {UPDATED}、JST）" data-en="Measured and written by teai.io · {DATE} (follow-up {UPDATED}, JST)">実測・執筆：teai.io · {DATE}（追試・改訂 {UPDATED}、JST）</span> · <a href="/blog" data-ja="実測記事一覧" data-en="More measured reports">実測記事一覧</a></footer>
<script>
(() => {{
  const en = new URLSearchParams(location.search).get('lang') === 'en';
  if (!en) return;
  document.documentElement.lang = 'en';
  document.title = 'Asking AI about building codes: paste the articles, leave the arithmetic to a tool. | teai';
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
