"""採点ロジック。すべて決定論的で、モデルの自己申告に依存しない。"""
from __future__ import annotations

import re

KANJI = "〇一二三四五六七八九"
KD = {c: i for i, c in enumerate(KANJI)}


def kanji_num(s: str) -> int | None:
    """'六十'->60, '百'->100, '千二百'->1200, '十二'->12"""
    if not s:
        return None
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in KD:
            num = KD[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        elif ch == "千":
            section += (num or 1) * 1000
            num = 0
        else:
            return None
    return total + section + num


def parse_fraction(text: str) -> float | None:
    """'6/10' '60/10' '0.6' '60%' '十分の六' '10分の6' を 0.6 に。"""
    if not text:
        return None
    t = text.replace(" ", "").replace("　", "").replace("．", ".")

    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if b:
            return a / b

    m = re.search(r"(\d+(?:\.\d+)?)\s*[%％]", t)
    if m:
        return float(m.group(1)) / 100

    m = re.search(r"([〇一二三四五六七八九十百千]+)分の([〇一二三四五六七八九十百千]+)", t)
    if m:
        a, b = kanji_num(m.group(1)), kanji_num(m.group(2))
        if a and b is not None:
            return b / a

    # '20分の1' のようにアラビア数字の「A分のB」
    m = re.search(r"(\d+)分の(\d+)", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a:
            return b / a

    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if m:
        v = float(m.group(1))
        return v / 10 if v > 1.5 else v
    return None


def parse_float(text: str) -> float | None:
    if not text:
        return None
    t = text.replace("．", ".").replace("，", ",")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


THREE_DP_RE = re.compile(r"\d+\.\d{3}\b")
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def daylight_candidates(text: str) -> list[float]:
    """採光補正係数の候補。回答は大きく2通りある。

    - 先頭に結論を書く型: 「**1.280**\\n計算過程: D/H = 1.34/3.0 = ...」
    - 末尾に結論を書く型: 「D/H = 1.34/3.0 = 0.447 ... = 1.28 → 1.280」

    どちらも正解にしたいので、回答中の最初の数値と最後の数値を候補にする。
    途中式の D を最初の数値として拾って系統的に過小評価していた不具合を直す
    （実測: Qwen3-8B 59.6%→69.5%）。3桁小数に限らないのは、2.87 のように
    桁を省いた正しい回答も拾うため。
    """
    if not text:
        return []
    vals = [float(m) for m in NUM_RE.findall(text.replace("．", ".").replace("，", ","))]
    if not vals:
        return []
    return [vals[0], vals[-1]] if vals[0] != vals[-1] else [vals[0]]


KANJI_NUM_RE = r"[〇一二三四五六七八九十百千]+"


def numeric_candidates(text: str) -> set[str]:
    """テキスト中の数値表現を answer_norm と同じキー空間に写す。"""
    out: set[str] = set()
    if not text:
        return out
    t = text.replace("　", " ").replace("．", ".")
    for m in re.finditer(rf"({KANJI_NUM_RE}分の{KANJI_NUM_RE}|\d+\s*分の\s*\d+|\d+\s*/\s*\d+)", t):
        f = parse_fraction(m.group(0))
        if f is not None:
            out.add(f"frac:{f:.6f}")
    for m in re.finditer(rf"{KANJI_NUM_RE}", t):
        n = kanji_num(m.group(0))
        if n is not None:
            out.add(f"num:{n}")
    for m in re.finditer(r"\d+(?:\.\d+)?", t):
        out.add(f"num:{float(m.group(0)):g}")
    return out


def norm_article(s: str) -> str:
    """'第56条の2' / '56条の2' / '第五十六条の二' -> '56の2'"""
    t = s.replace("第", "").replace("条", "").replace(" ", "").replace("　", "")
    t = t.replace("の二", "の2").replace("の三", "の3").replace("の四", "の4")
    # 漢数字の条番号
    m = re.fullmatch(r"([〇一二三四五六七八九十百千]+)(の\d+)?", t)
    if m:
        n = kanji_num(m.group(1))
        if n is not None:
            return f"{n}{m.group(2) or ''}"
    return t


ART_REF = re.compile(r"第?\s*([0-9〇一二三四五六七八九十百千]+(?:の[0-9一二三四五六七八九十]+)?)\s*条")


def extract_articles(p: str) -> set[str]:
    """回答文から条番号を抽出する。

    「第56条の2」のように枝番が「条」の後ろに来る表記と、「第56の2条」の両方を
    同じ '56の2' に正規化する（実測: 正解『建築基準法第56の2条』に対して
    『建築基準法第56条の2』と答えた回答が、条番号不一致として落ちていた）。
    """
    q = re.sub(r"第\s*([0-9〇一二三四五六七八九十百千]+)\s*条\s*の\s*([0-9一二三四五六七八九十]+)",
               r"第\1の\2条", p)
    return {norm_article(m.group(1)) for m in ART_REF.finditer(q)}


def grade(task: dict, pred: str) -> tuple[bool, str]:
    kind = task["kind"]
    if pred is None:
        return False, "empty"
    p = pred.strip()

    if kind == "numeric":
        want = task["answer_norm"]
        # 回答中の数値表現を全部拾って、どれかが一致すれば正解
        cands = numeric_candidates(p)
        return (want in cands), ("ok" if want in cands else f"want={want} got={sorted(cands)[:6]}")

    if kind == "calc_daylight":
        want = float(task["answer"])
        cands = daylight_candidates(p)
        if not cands:
            return False, "no-number"
        ok = any(abs(v - want) <= 0.011 for v in cands)
        return ok, f"want={want} got={cands}"

    if kind in ("calc_bcr", "calc_far"):
        v = parse_fraction(p)
        if v is None:
            return False, "no-number"
        want = float(task["answer_norm"].split(":")[1])
        return abs(v - want) <= 0.0051, f"want={want} got={v}"

    if kind == "applied":
        # 「延べ面積=○m²、建築面積=○m²」の2値を両方要求する。片方だけ合っても
        # 不正解（2段の計算を片方だけ当てた回答を正解にしない）。
        _, want_total, want_bldg = task["answer_norm"].split("#")
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", p.replace(",", ""))
        vals = [float(x) for x in nums]
        total_ok = any(abs(v - float(want_total)) <= 0.11 for v in vals)
        bldg_ok = any(abs(v - float(want_bldg)) <= 0.11 for v in vals)
        return (total_ok and bldg_ok), f"total={total_ok} bldg={bldg_ok} vals={vals[:6]}"

    if kind == "cite":
        want_law, want_art = task["answer_norm"].split("#")
        # 法令名（施行令かどうか）を判定
        # 法令名の明示が無い場合は「施行令」と書いていないことだけ確認する
        # （条番号だけの回答は許容。ただし令の条文を法の条文として答えたら不正解）
        if want_law == "建築基準法施行令":
            law_ok = "施行令" in p
        else:
            law_ok = "施行令" not in p
        arts = extract_articles(p)
        ok = law_ok and (want_art in arts)
        return ok, f"want={want_law}#{want_art} law_ok={law_ok} arts={sorted(arts)[:4]}"

    if kind == "false_claim":
        # 誤りと指摘し、正しい値と条番号を両方示せたら正解
        said_wrong = bool(re.search(r"誤り|誤って|不正確|正しくない|incorrect|wrong", p))
        _, _want_law, want_art, want_num = task["answer_norm"].split("#")
        arts = extract_articles(p)
        # 「正しい数値」「正しくは」の直後だけを見る。全文を走査すると、無関係な
        # 数値（他の条項の引用など）が偶然一致して誤って正解になる
        # （実測: false-003 は正解「一」なのに「五百平方メートル」だけで通った）。
        # ただし「正しくは次のとおり。『…六十メートル…』」のように、キーワード直後が
        # 句点で切れて数値が次の文に来る回答がある（実測: false-010 は正解なのに
        # num=False で落ちた）。最初の文に数値が無ければ次の文まで見る。
        m = re.search(r"(?:正しい数値|正しくは|correct(?:ed)? value)(.{0,200})", p, re.S)
        scope = p
        if m:
            # 箇条書きで改行の直後に数値が来る回答がある（実測: false-014）。
            # 最初に数値が出る断片を採る。
            for frag in re.split(r"[。\n]", m.group(1)):
                if numeric_candidates(frag):
                    scope = frag
                    break
            else:
                scope = m.group(1)
        num_ok = want_num in numeric_candidates(scope)
        ok = said_wrong and num_ok and (want_art in arts)
        return ok, f"wrong={said_wrong} num={num_ok} art={want_art in arts}"

    if kind == "calc_sky_grade":
        # 「ANSWER: 72.4%」を優先、なければ最後の％
        m = re.search(r"ANSWER\s*[:：]\s*(\d+(?:\.\d+)?)\s*[%％]?", p)
        pcts = [m.group(1)] if m else re.findall(r"(\d+(?:\.\d+)?)\s*[%％]", p.replace(",", ""))
        if not pcts:
            return False, "no-percent"
        v = float(pcts[-1])
        want = float(task["answer_norm"].split(":")[1])
        return abs(v - want) <= 0.3, f"want={want:.2f} got={v}"

    if kind == "sky_points_2road":
        want = task["answer_norm"].split(":")[1]
        m = re.findall(r"ANSWER\s*[:：]\s*(\d+)\s*[,，、]\s*(\d+)\s*[,，、]\s*(\d+)\s*[,，、]\s*(\d+)", p)
        if not m:
            return False, "no-answer-line"
        got = ",".join(m[-1])
        return got == want, f"want={want} got={got}"

    if kind == "calc_sky":
        # 「%」の付いた数値を優先（途中計算の数値を拾わない）。最後に出たものを答えとみなす。
        pcts = re.findall(r"(\d+(?:\.\d+)?)\s*[%％]", p.replace(",", ""))
        if not pcts:
            return False, "no-percent"
        v = float(pcts[-1])
        want = float(task["answer_norm"].split(":")[1])
        return abs(v - want) <= 0.3, f"want={want:.2f} got={v}"

    if kind == "sky_compare":
        _, rest = task["answer_norm"].split(":")
        want_ok, want_p = rest.split("#")
        m = re.search(r"判定\s*[:：]?\s*(不適合|適合)", p)
        verdict = m.group(1) if m else ("不適合" if "不適合" in p else ("適合" if "適合" in p else None))
        mp = re.search(r"(?:最も|最小|小さい|不利)[^P\n]{0,40}P\s*([1-3])", p) or re.search(r"P\s*([1-3])\s*$", p)
        got_p = mp.group(1) if mp else None
        v_ok = verdict == ("適合" if want_ok == "1" else "不適合")
        p_ok = got_p == want_p
        return (v_ok and p_ok), f"verdict={verdict} point=P{got_p} want={want_ok}#P{want_p}"

    if kind == "sky_points":
        _, rest = task["answer_norm"].split(":")
        want_n, want_gap = rest.split("#")
        # 「7点」「数：7」「7\n5.00」のどれでも読む（実測: 単位なしの正答を落としていた）。
        # 条番号（135・9・1項）を拾わないよう、条文の引用部分は除いてから数値を読む。
        q = re.sub(r"第?[0-9一二三四五六七八九十百]+条(?:の[0-9一二三四五六七八九十]+)?(?:第?[0-9一二三四五六七八九十]+項)?", " ", p.replace("*", ""))
        mn = (re.search(r"(\d+)\s*(?:点|箇所|か所|カ所)", q)
              or re.search(r"数[^0-9\n]{0,6}(\d+)(?![.\d])", q)
              or re.search(r"^\s*(\d+)\s*$", q, re.M))
        nums = [float(x) for x in re.findall(r"(\d+\.\d+|\d+)\s*(?:m|ｍ|メートル)?", q)]
        n_ok = bool(mn) and int(mn.group(1)) == int(want_n)
        g_ok = any(abs(x - float(want_gap)) <= 0.011 for x in nums if x != int(want_n) or float(want_gap) == int(want_n))
        return (n_ok and g_ok), f"n={mn.group(1) if mn else None} gaps={nums[:4]} want={want_n}#{float(want_gap):.2f}"

    return False, f"unknown-kind:{kind}"
