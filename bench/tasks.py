#!/usr/bin/env python3
"""建築基準法の条文から、正解が機械的に決まるベンチマークタスクを生成する。

方針: 正解は必ず条文テキストか、条文を実装した決定論的計算から作る。
LLMに正解を書かせない（幻覚を正解にしない）。各タスクは evidence に
根拠条文と引用文を持つので、後から人手で監査できる。

出力: data/tasks.jsonl
"""
from __future__ import annotations

import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ARTICLES = os.path.join(ROOT, "corpus", "articles.jsonl")
OUT = os.path.join(ROOT, "data", "tasks.jsonl")

SEED = 20261031

# ---------------------------------------------------------------- 表記ユーティリティ

KANJI = "〇一二三四五六七八九"
KANJI_DIGITS = {c: i for i, c in enumerate(KANJI)}


def kanji_num(s: str) -> int | None:
    """'二十分の一' の分子分母、'百二十' などの漢数字を int に。"""
    if not s:
        return None
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in KANJI_DIGITS:
            num = KANJI_DIGITS[ch]
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


def frac(s: str) -> float | None:
    """'十分の三' -> 0.3 / '二十分の一' -> 0.05"""
    m = re.fullmatch(r"([〇一二三四五六七八九十百千]+)分の([〇一二三四五六七八九十百千]+)", s)
    if not m:
        return None
    a, b = kanji_num(m.group(1)), kanji_num(m.group(2))
    if not a or b is None:
        return None
    return b / a


def law_slug(law: str) -> str:
    """法令名 -> ID用の短縮名。'建築基準法'[:4] と '建築基準法施行令'[:4] が
    同じ '建築基準' になり、法20条と令20条のIDが衝突していたため明示的に分ける。"""
    return "hou" if law == "建築基準法" else "rei"


def load_articles() -> dict[tuple[str, str], dict]:
    idx = {}
    for line in open(ARTICLES, encoding="utf-8"):
        r = json.loads(line)
        idx[(r["law"], r["article"])] = r
    return idx


# ---------------------------------------------------------------- 決定論的計算エンジン

# 法53条1項: 用途地域 -> 建蔽率の選択肢（都市計画で定める）
ZONE_BCR = {
    "第一種低層住居専用地域": [0.3, 0.4, 0.5, 0.6],
    "第二種低層住居専用地域": [0.3, 0.4, 0.5, 0.6],
    "第一種中高層住居専用地域": [0.3, 0.4, 0.5, 0.6],
    "第二種中高層住居専用地域": [0.3, 0.4, 0.5, 0.6],
    "田園住居地域": [0.3, 0.4, 0.5, 0.6],
    "工業専用地域": [0.3, 0.4, 0.5, 0.6],
    "第一種住居地域": [0.5, 0.6, 0.8],
    "第二種住居地域": [0.5, 0.6, 0.8],
    "準住居地域": [0.5, 0.6, 0.8],
    "準工業地域": [0.5, 0.6, 0.8],
    "近隣商業地域": [0.6, 0.8],
    "商業地域": [0.8],
    "工業地域": [0.5, 0.6],
}

# 法52条1項: 用途地域 -> 容積率の選択肢
ZONE_FAR = {
    "第一種低層住居専用地域": [0.5, 0.6, 0.8, 1.0, 1.5, 2.0],
    "第二種低層住居専用地域": [0.5, 0.6, 0.8, 1.0, 1.5, 2.0],
    "田園住居地域": [0.5, 0.6, 0.8, 1.0, 1.5, 2.0],
    "第一種中高層住居専用地域": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "第二種中高層住居専用地域": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "第一種住居地域": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "第二種住居地域": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "準住居地域": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "近隣商業地域": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "準工業地域": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "商業地域": [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0],
    "工業地域": [1.0, 1.5, 2.0, 3.0, 4.0],
    "工業専用地域": [1.0, 1.5, 2.0, 3.0, 4.0],
}

# 法52条2項: 前面道路幅員12m未満のときの容積率乗数
ROAD_FAR_MULT = {
    "第一種低層住居専用地域": 0.4,
    "第二種低層住居専用地域": 0.4,
    "田園住居地域": 0.4,
    "第一種中高層住居専用地域": 0.4,
    "第二種中高層住居専用地域": 0.4,
    "第一種住居地域": 0.4,
    "第二種住居地域": 0.4,
    "準住居地域": 0.4,
}
ROAD_FAR_MULT_OTHER = 0.6  # その他の建築物

# 令20条: 採光補正係数
RESIDENTIAL_ZONES = {
    "第一種低層住居専用地域", "第二種低層住居専用地域", "第一種中高層住居専用地域",
    "第二種中高層住居専用地域", "第一種住居地域", "第二種住居地域", "準住居地域", "田園住居地域",
}
INDUSTRIAL_ZONES = {"準工業地域", "工業地域", "工業専用地域"}
COMMERCIAL_ZONES = {"近隣商業地域", "商業地域", "用途地域の指定のない区域"}


def daylight_coefficient(zone: str, d: float, h: float, faces_road: bool,
                         skylight: bool = False, engawa: bool = False) -> float:
    """令20条2項の採光補正係数。d=水平距離(m), h=垂直距離(m)。

    住居系: 6.0*D/H-1.4 / 準工業・工業系: 8.0*D/H-1.0 / 商業系: 10.0*D/H-1.0
    道に面する場合の下限1.0、道に面しない場合の下限は距離条件つき。上限3.0。
    """
    if h <= 0:
        raise ValueError("h must be > 0")
    r = d / h
    if zone in RESIDENTIAL_ZONES:
        v, road_min, off_road_min = 6.0 * r - 1.4, 7.0, 7.0
    elif zone in INDUSTRIAL_ZONES:
        v, road_min, off_road_min = 8.0 * r - 1.0, 5.0, 5.0
    elif zone in COMMERCIAL_ZONES:
        v, road_min, off_road_min = 10.0 * r - 1.0, 4.0, 4.0
    else:
        raise ValueError(f"unknown zone: {zone}")

    if faces_road:
        if v < 1.0:
            v = 1.0
    else:
        if d >= off_road_min:
            if v < 1.0:
                v = 1.0
        else:
            if v < 0:
                v = 0.0
    if skylight:
        v *= 3.0
    if engawa:
        v *= 0.7
    return min(v, 3.0)


def bcr_limit(zone: str, planned: float, fireproof: bool = False,
              quasi_fireproof: bool = False, corner: bool = False,
              in_fire_zone: bool = False, in_quasi_fire_zone: bool = False) -> float:
    """法53条1項+3項の建蔽率の限度。"""
    base = planned
    add = 0.0
    # 3項一号: 防火地域（限度が8/10の地域を除く）内の耐火建築物等 / 準防火地域内の耐火・準耐火
    if in_fire_zone and fireproof and base != 0.8:
        add += 0.1
    if in_quasi_fire_zone and (fireproof or quasi_fireproof):
        add += 0.1
    # 3項二号: 角地
    if corner:
        add += 0.1
    return min(base + add, 1.0) if add else base


def far_limit(zone: str, planned: float, road_width: float | None = None) -> float:
    """法52条1項+2項の容積率の限度。"""
    if road_width is None or road_width >= 12:
        return planned
    mult = ROAD_FAR_MULT.get(zone, ROAD_FAR_MULT_OTHER)
    return min(planned, road_width * mult)


# ---------------------------------------------------------------- タスク生成

NUM_TOKEN = r"(?:[〇一二三四五六七八九十百千]+分の[〇一二三四五六七八九十百千]+|[〇一二三四五六七八九十百千]+)"

# 規制値として意味を持つ数値の直後に来る語。これが無い数値（条番号・項番・
# 単なる列挙番号）は伏せ字にしない。実測で「第五十九条」の「五」や
# 段落番号「２」を正解にしてしまう事故が出たため。
REG_UNITS = ("メートル", "センチメートル", "平方メートル", "立方メートル", "階")
# 「十以上」「二十以下」のように、2桁以上の数値に付く比較語は規制値。
# 「一以上」「二以上」は列挙の言い換えで、法令知識を問う値にならないので除外する。
REG_COMPARE = ("以上", "以下", "以内", "未満", "を超え")
# 直前がこれなら参照番号（第七条・前項・同条など）
REF_PREFIX = ("第", "前", "同", "各", "次", "当該")


def is_regulatory(s: str, m: re.Match) -> bool:
    """その数値が規制値（伏せてよい数値）かどうか。

    除外するもの: 条番号・項番（直前が「第」「前」「同」等）、小数の途中
    （前後が「・」）、列挙の「一以上」「二以上」。
    """
    before = s[max(0, m.start() - 2):m.start()]
    if any(before.endswith(p) for p in REF_PREFIX):
        return False
    if before.endswith("・") or s[m.end():m.end() + 1] == "・":
        return False  # 「三・〇」の一部
    after = s[m.end():m.end() + 6]
    if after.startswith(("項", "号", "条")):
        return False
    if "分の" in m.group(0):
        return True
    if any(after.startswith(u) for u in REG_UNITS):
        return True
    if any(after.startswith(u) for u in REG_COMPARE):
        # 2桁以上（十・百・千を含む）だけ規制値として採用
        return any(c in m.group(0) for c in "十百千")
    return False


def gen_numeric_cloze(idx, rng) -> list[dict]:
    """条文中の数値表現を伏せて答えさせる（法令数値の暗記・検索）。"""
    targets = [
        ("建築基準法", "20"), ("建築基準法", "26"), ("建築基準法", "27"),
        ("建築基準法", "28"), ("建築基準法", "35"), ("建築基準法", "52"),
        ("建築基準法", "53"), ("建築基準法", "55"), ("建築基準法", "56"),
        ("建築基準法施行令", "19"), ("建築基準法施行令", "20"),
        ("建築基準法施行令", "23"), ("建築基準法施行令", "120"),
        ("建築基準法施行令", "121"), ("建築基準法施行令", "125"),
        ("建築基準法施行令", "126"),
    ]
    out = []
    for law, art in targets:
        r = idx.get((law, art))
        if not r:
            continue
        topic = r["caption"].strip("（）") or f"第{art}条"
        short = "建築基準法" if law == "建築基準法" else "建築基準法施行令"
        sents = re.split(r"(?<=。) ", r["text"])
        for s in sents:
            if len(s) < 30 or len(s) > 200:
                continue
            toks = [m for m in re.finditer(NUM_TOKEN, s) if is_regulatory(s, m)]
            if not toks:
                continue
            for t in toks:
                if "分の" not in t.group(0):
                    n = kanji_num(t.group(0))
                    if n is not None and n <= 2:
                        continue  # 「二以上」等は法令知識を問う値にならない
                masked = s[:t.start()] + "【　】" + s[t.end():]
                out.append({
                    "id": f"num-{law_slug(law)}-{art}-{t.start()}",
                    "kind": "numeric",
                    "topic": topic,
                    "question": f"{short}（{topic}）について、次の条文の【　】に入る数値は何か。"
                                f"数値だけ答えよ。\n\n「{masked}」",
                    "answer": t.group(0),
                    "answer_norm": norm_numeric(t.group(0)),
                    "evidence": {"law": law, "article": art, "quote": s},
                })
    return out


def norm_numeric(s: str) -> str:
    """'二十分の一' と '1/20' と '20分の1' を同じキーにする。"""
    s = s.strip().replace(" ", "").replace("　", "")
    f = frac(s)
    if f is not None:
        return f"frac:{f:.6f}"
    n = kanji_num(s)
    if n is not None:
        return f"num:{n}"
    # アラビア数字
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    if m:
        return f"frac:{int(m.group(2))/int(m.group(1)):.6f}" if False else f"frac:{int(m.group(1))/int(m.group(2)):.6f}"
    try:
        return f"num:{float(s):g}"
    except ValueError:
        return f"raw:{s}"


def gen_daylight(rng, n: int = 40) -> list[dict]:
    """令20条 採光補正係数の計算。"""
    out = []
    zones = sorted(RESIDENTIAL_ZONES | INDUSTRIAL_ZONES | COMMERCIAL_ZONES)
    for i in range(n):
        # 上限3.0や下限0.0に張り付くと全モデルが当たって識別力が消えるので、
        # 0.1〜2.9 に入るまで抽選し直す（実測で40件中34件が3.000になっていた）。
        for _ in range(200):
            zone = rng.choice(zones)
            h = rng.choice([1.5, 2.0, 2.5, 3.0, 4.0, 5.0])
            d = round(rng.uniform(0.1, 1.6) * h, 2)
            faces_road = rng.random() < 0.5
            sky = rng.random() < 0.15
            eng = (not sky) and rng.random() < 0.15
            v = daylight_coefficient(zone, d, h, faces_road, sky, eng)
            # 1.000 は「道に面する場合の下限」で機械的に出やすいので避ける
            if 0.1 <= v <= 2.9 and abs(v - 1.0) > 1e-9:
                break
        cond = []
        if sky:
            cond.append("天窓である")
        if eng:
            cond.append("外側に幅90cm以上の縁側がある")
        out.append({
            "id": f"day-{i:03d}",
            "kind": "calc_daylight",
            "topic": "採光補正係数",
            "question": (
                "建築基準法施行令第20条2項により、次の開口部の採光補正係数を計算せよ。\n"
                f"・用途地域: {zone}\n"
                f"・開口部から隣地境界線（または対向部）までの水平距離 D = {d} m\n"
                f"・開口部の中心までの垂直距離 H = {h} m\n"
                f"・開口部は{'道路に面する' if faces_road else '道路に面しない'}\n"
                + (f"・{'、'.join(cond)}\n" if cond else "")
                + "小数第3位までで答えよ（例: 1.234）。"
            ),
            "answer": f"{v:.3f}",
            "answer_norm": f"num:{v:.2f}",
            "evidence": {
                "law": "建築基準法施行令", "article": "20",
                "calc": {"zone": zone, "d": d, "h": h, "faces_road": faces_road,
                         "skylight": sky, "engawa": eng},
            },
        })
    return out


def gen_bcr(rng, n: int = 30) -> list[dict]:
    """法53条 建蔽率の限度（3項の緩和込み）。"""
    out = []
    for i in range(n):
        zone = rng.choice(list(ZONE_BCR))
        planned = rng.choice(ZONE_BCR[zone])
        in_fire = rng.random() < 0.35
        in_quasi = (not in_fire) and rng.random() < 0.35
        fire = rng.random() < 0.6
        quasi = (not fire) and rng.random() < 0.5
        corner = rng.random() < 0.3
        v = bcr_limit(zone, planned, fire, quasi, corner, in_fire, in_quasi)
        flags = []
        if in_fire:
            flags.append("防火地域内")
        elif in_quasi:
            flags.append("準防火地域内")
        else:
            flags.append("防火地域・準防火地域のいずれでもない")
        flags.append("耐火建築物" if fire else ("準耐火建築物" if quasi else "その他の建築物"))
        flags.append("街区の角にある敷地" if corner else "角地ではない")
        out.append({
            "id": f"bcr-{i:03d}",
            "kind": "calc_bcr",
            "topic": "建蔽率",
            "question": (
                "建築基準法第53条により、次の建築物の建蔽率の限度を求めよ。\n"
                f"・用途地域: {zone}\n"
                f"・都市計画で定められた建蔽率: {int(planned*10)}/10\n"
                f"・{'、'.join(flags)}\n"
                "割合を分数（例: 6/10）で答えよ。"
            ),
            "answer": f"{round(v*10)}/10",
            "answer_norm": f"frac:{v:.6f}",
            "evidence": {
                "law": "建築基準法", "article": "53",
                "calc": {"zone": zone, "planned": planned, "fireproof": fire,
                         "quasi_fireproof": quasi, "corner": corner,
                         "in_fire_zone": in_fire, "in_quasi_fire_zone": in_quasi},
            },
        })
    return out


def gen_far(rng, n: int = 30) -> list[dict]:
    """法52条 容積率の限度（前面道路制限込み）。"""
    out = []
    for i in range(n):
        zone = rng.choice(list(ZONE_FAR))
        planned = rng.choice(ZONE_FAR[zone])
        width = rng.choice([4.0, 5.0, 6.0, 8.0, 10.0, 11.0, 12.0, 16.0])
        v = far_limit(zone, planned, width)
        out.append({
            "id": f"far-{i:03d}",
            "kind": "calc_far",
            "topic": "容積率",
            "question": (
                "建築基準法第52条により、次の建築物の容積率の限度を求めよ。\n"
                f"・用途地域: {zone}\n"
                f"・都市計画で定められた容積率: {int(planned*10)}/10\n"
                f"・前面道路の幅員: {width:g} m（特定道路の加算は考慮しない）\n"
                "割合を分数（例: 40/10）で答えよ。"
            ),
            "answer": f"{round(v*10)}/10",
            "answer_norm": f"frac:{v:.6f}",
            "evidence": {
                "law": "建築基準法", "article": "52",
                "calc": {"zone": zone, "planned": planned, "road_width": width},
            },
        })
    return out


def gen_applied(rng, n: int = 30) -> list[dict]:
    """応用: 法52条・53条を組み合わせて、実際の敷地での上限面積を出す。

    単発の公式当てはめは RAG を渡すと全モデルがほぼ満点になり、モデル選定の
    識別力が無くなった（実測: RAG で 0.96〜0.99）。ここでは
    ①前面道路制限で容積率を決める ②敷地面積を掛ける ③建蔽率と比較する、
    の複数段を要求する。
    """
    out = []
    for i in range(n):
        zone = rng.choice([z for z in ZONE_FAR if z != "商業地域"])
        planned_far = rng.choice(ZONE_FAR[zone])
        planned_bcr = rng.choice(ZONE_BCR[zone])
        width = rng.choice([4.0, 5.0, 6.0, 8.0, 10.0, 11.0])
        site = rng.choice([120, 150, 180, 200, 240, 300, 360, 400, 500])
        far = far_limit(zone, planned_far, width)
        bcr = bcr_limit(zone, planned_bcr)
        total = round(site * far, 1)
        bldg = round(site * bcr, 1)
        out.append({
            "id": f"app-{i:03d}",
            "kind": "applied",
            "topic": "面積上限の算定",
            "question": (
                "建築基準法第52条・第53条により、次の敷地に建てられる建築物の"
                "「延べ面積の上限」と「建築面積の上限」を求めよ。\n"
                f"・用途地域: {zone}\n"
                f"・敷地面積: {site} m²\n"
                f"・都市計画で定められた容積率: {int(planned_far*10)}/10\n"
                f"・都市計画で定められた建蔽率: {int(planned_bcr*10)}/10\n"
                f"・前面道路の幅員: {width:g} m（特定道路の加算は考慮しない）\n"
                "・防火地域・準防火地域の指定なし、角地ではない\n"
                "「延べ面積=○m²、建築面積=○m²」の形式で、小数第1位まで答えよ。"
            ),
            "answer": f"延べ面積={total}m²、建築面積={bldg}m²",
            "answer_norm": f"applied#{total}#{bldg}",
            "evidence": {
                "law": "建築基準法", "article": "52",
                "calc": {"zone": zone, "site": site, "planned_far": planned_far,
                         "planned_bcr": planned_bcr, "road_width": width,
                         "far": far, "bcr": bcr},
            },
        })
    return out


# 条文同定の対象。見出し（caption）はコーパスから引くので、ここには
# 「どの条文を出すか」だけを書く。手書きの見出しは実測で複数取り違えた
# （例: 57条を「高度地区」、62条を「準防火地域内の建築物」と誤記）。
CITE_TARGETS = [
    ("建築基準法", "6"), ("建築基準法", "20"), ("建築基準法", "26"),
    ("建築基準法", "27"), ("建築基準法", "28"), ("建築基準法", "35"),
    ("建築基準法", "42"), ("建築基準法", "43"), ("建築基準法", "48"),
    ("建築基準法", "52"), ("建築基準法", "53"), ("建築基準法", "55"),
    ("建築基準法", "56"), ("建築基準法", "56の2"), ("建築基準法", "59"),
    ("建築基準法", "61"), ("建築基準法", "62"), ("建築基準法", "86"),
    ("建築基準法", "87"),
    ("建築基準法施行令", "19"), ("建築基準法施行令", "20"),
    ("建築基準法施行令", "28"), ("建築基準法施行令", "120"),
    ("建築基準法施行令", "121"), ("建築基準法施行令", "125"),
    ("建築基準法施行令", "126"), ("建築基準法施行令", "132"),
]


def gen_cite(idx, rng) -> list[dict]:
    """条文同定: 見出しから法令名と条番号を答える。"""
    out = []
    for law, art in CITE_TARGETS:
        r = idx.get((law, art))
        if not r:
            continue
        topic = r["caption"].strip("（）")
        out.append({
            "id": f"cite-{law_slug(law)}-{art}",
            "kind": "cite",
            "topic": "条文同定",
            "question": (
                f"建築基準法の条文を特定したい。次の内容はどの条文に規定されているか。"
                f"「{law}」第〇条の形で条番号まで答えよ。\n\n内容: {topic}"
            ),
            "answer": f"{law}第{art}条",
            "answer_norm": f"{law}#{art}",
            "evidence": {"law": law, "article": art},
        })
    return out


def gen_false_claim(idx, rng, n: int = 30) -> list[dict]:
    """誤り検出: 条文の数値を1つだけ改変した記述を提示し、正誤と根拠条文を答えさせる。"""
    pool = []
    for (law, art), r in idx.items():
        if law != "建築基準法":
            continue
        for s in re.split(r"(?<=。) ", r["text"]):
            for m in re.finditer(NUM_TOKEN, s):
                # 同じ数値が文中に複数回出ると、どれを直すのか一意に定まらず
                # 採点も曖昧になる（実測: false-008 は「三」が2箇所あった）。
                if is_regulatory(s, m) and 30 < len(s) < 160 and s.count(m.group(0)) == 1:
                    pool.append((law, art, s, m))
    rng.shuffle(pool)
    out = []
    for i, (law, art, s, t) in enumerate(pool):
        if len(out) >= n:
            break
        orig = t.group(0)
        f = frac(orig)
        if f is not None:
            # 分数は分子を変える（1/20 -> 1/10 など）
            m = re.fullmatch(r"([〇一二三四五六七八九十百千]+)分の([〇一二三四五六七八九十百千]+)", orig)
            den = kanji_num(m.group(1))
            cand = [d for d in (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 30, 50, 100) if d != den]
            new = f"{knum(rng.choice(cand))}分の{m.group(2)}"
        else:
            v = kanji_num(orig)
            if v is None:
                continue
            # 「一」「二」は号番号・列挙の言い換えであることが多く、法令知識を
            # 問う問題にならない（実測: 正解が「一」で、第2条6号の号番号だった）。
            if v <= 2:
                continue
            new = knum(max(1, v + rng.choice([-2, -1, 1, 2])))
        mutated = s[:t.start()] + new + s[t.end():]
        out.append({
            "id": f"false-{i:03d}",
            "kind": "false_claim",
            "topic": "誤り検出",
            "question": (
                "次の記述は建築基準法に照らして正しいか。誤りがある場合は正しい数値と根拠条文"
                "（法令名と条番号）を示せ。\n\n"
                f"「{mutated}」"
            ),
            "answer": f"誤り。正しくは「{orig}」（{law}第{art}条）",
            "answer_norm": f"wrong#{law}#{art}#{norm_numeric(orig)}",
            "evidence": {"law": law, "article": art, "quote": s, "mutated": mutated},
        })
    return out


def knum(n: int) -> str:
    """int -> 漢数字（法令表記に寄せる）。"""
    if n <= 0:
        return "〇"
    digits = "〇一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 100:
        t, o = divmod(n, 10)
        return ("" if t == 1 else digits[t]) + "十" + ("" if o == 0 else digits[o])
    if n < 1000:
        h, r = divmod(n, 100)
        s = ("" if h == 1 else digits[h]) + "百"
        if r == 0:
            return s
        if r < 10:
            return s + digits[r]
        t, o = divmod(r, 10)
        return s + ("" if t == 1 else digits[t]) + "十" + ("" if o == 0 else digits[o])
    th, r = divmod(n, 1000)
    return ("" if th == 1 else digits[th]) + "千" + (knum(r) if r else "")


def main() -> int:
    rng = random.Random(SEED)
    idx = load_articles()
    tasks = []
    tasks += gen_numeric_cloze(idx, rng)
    tasks += gen_daylight(rng)
    tasks += gen_bcr(rng)
    tasks += gen_far(rng)
    tasks += gen_cite(idx, rng)
    tasks += gen_false_claim(idx, rng)
    tasks += gen_applied(rng)

    seen = set()
    uniq = []
    for t in tasks:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        uniq.append(t)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for t in uniq:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    from collections import Counter
    c = Counter(t["kind"] for t in uniq)
    print(f"wrote {len(uniq)} tasks -> {OUT}", file=sys.stderr)
    for k, v in sorted(c.items()):
        print(f"  {k}: {v}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
