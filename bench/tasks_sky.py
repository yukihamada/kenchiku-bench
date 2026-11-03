"""天空率タスク（data/tasks_sky.jsonl）を生成する。正解はすべて bench/sky.py の計算から。

kind:
  calc_sky     直方体1〜2棟の天空率を％で答える（±0.3ポイント）
  sky_compare  計画建築物と道路高さ制限適合建築物を3測定点で比べ、適合判定＋差が最小の測定点
  sky_points   令135条の9の測定点の最少数と間隔
既存の203問（data/tasks.jsonl）とは別ファイルにして、公開済みの結果を変えない。
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from sky import measurement_points, sky_ratio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "tasks_sky.jsonl")
SEED = 135

FRAME = (
    "座標系（単位m）: x軸は前面道路に沿う方向、y軸は道路から敷地の奥へ向かう方向。"
    "前面道路は y=-{w}〜0（幅員{w}m、平坦）、敷地は y≥0。"
    "高さは前面道路の路面の中心からの高さ。地盤は平坦で、建物以外に天空を遮るものはない。"
)


def fmt_box(b: dict) -> str:
    return f"x={b['x0']:g}〜{b['x1']:g}, y={b['y0']:g}〜{b['y1']:g}, 高さ{b['h']:g}m の直方体"


def gen_calc(rng: random.Random, n: int = 20) -> list[dict]:
    out = []
    while len(out) < n:
        w = rng.choice([4, 5, 6, 8, 10, 12])
        x0 = rng.choice([0, 2, 4])
        width = rng.choice([8, 10, 12, 15, 20])
        y0 = rng.choice([0, 1, 2, 3])
        depth = rng.choice([8, 10, 12, 15])
        h = rng.choice([7, 9, 10, 12, 15, 18, 24])
        boxes = [dict(x0=x0, x1=x0 + width, y0=y0, y1=y0 + depth, h=h)]
        if rng.random() < 0.35:  # 上に載る塔屋状の2棟目
            bx = x0 + rng.choice([2, 3]); by = y0 + rng.choice([3, 4])
            boxes.append(dict(x0=bx, x1=bx + rng.choice([3, 4, 5]), y0=by,
                              y1=by + rng.choice([3, 4]), h=h + rng.choice([3, 6, 9])))
        px = rng.choice([x0, x0 + width / 2, x0 + width])
        py = -w
        rs = sky_ratio(px, py, boxes)
        bl = "\n".join(f"- 建物{i+1}: {fmt_box(b)}" for i, b in enumerate(boxes))
        q = (
            "建築基準法施行令第135条の5の天空率を計算せよ。\n"
            + FRAME.format(w=w)
            + f"\n建築物:\n{bl}\n"
            + f"測定点（想定半球の中心）: (x={px:g}, y={py:g})、路面の中心の高さ。\n"
            + "天空率をパーセントで小数第1位まで答えよ（例: 72.4%）。"
        )
        out.append({
            "id": f"sky-calc-{len(out)+1:03d}", "kind": "calc_sky", "topic": "天空率",
            "question": q, "answer": f"{rs*100:.1f}%", "answer_norm": f"pct:{rs*100:.4f}",
            "geometry": {"point": [px, py], "boxes": boxes, "road_width": w},
            "evidence": {"law": "建築基準法施行令", "article": "135の5",
                         "calc": "方位角0.01°刻みの正射影積分（bench/sky.py）・解析解で検算済み"},
        })
    return out


def gen_compare(rng: random.Random, n: int = 20) -> list[dict]:
    out = []
    tries = 0
    want_ok = True
    while len(out) < n and tries < 5000:
        tries += 1
        w = rng.choice([6, 8, 10, 12])
        slope = 1.25
        L = rng.choice([12, 15, 18])  # 間口
        depth = rng.choice([12, 15, 18])
        # 道路高さ制限適合建築物: 奥行を3段に分け、各段の手前で斜線の高さに揃えた段状
        band = depth / 3
        conf = []
        for k in range(3):
            ya = k * band
            conf.append(dict(x0=0, x1=L, y0=ya, y1=ya + band, h=round(slope * (w + ya), 2)))
        # 計画建築物: 後退 s、間口を狭め、高く（段なしの箱）
        s = rng.choice([1, 1.5, 2, 3])
        sl, sr = rng.sample([0.5, 1, 1.5, 2, 3], 2)  # 左右で違う空き（対称だとP1とP3が同値になる）
        h = round(slope * (w + depth) * rng.uniform(0.75, 1.05), 1)
        plan = [dict(x0=sl, x1=L - sr, y0=s, y1=depth, h=h)]
        pts = [(0, -w), (L / 2, -w), (L, -w)]
        rows = []
        for (px, py) in pts:
            a, c = sky_ratio(px, py, plan, steps=7200), sky_ratio(px, py, conf, steps=7200)
            rows.append((a, c))
        diffs = [a - c for a, c in rows]
        if min(abs(d) for d in diffs) < 0.003:  # 0.3ポイント未満の際どい問題は作らない
            continue
        sd = sorted(diffs)
        if sd[1] - sd[0] < 0.002:  # 差が最小の点が0.2ポイント以内で並ぶ問題は作らない
            continue
        ok = all(d >= 0 for d in diffs)
        if ok != want_ok:
            continue
        want_ok = not want_ok
        rows = [(sky_ratio(px, py, plan), sky_ratio(px, py, conf)) for (px, py) in pts]
        diffs = [a - c for a, c in rows]
        ok = all(d >= 0 for d in diffs)
        imin = min(range(3), key=lambda i: diffs[i])
        q = (
            "建築基準法施行令第135条の6第1項第1号の天空率による比較をせよ"
            "（後退距離の要件は満たしているものとする）。\n"
            + FRAME.format(w=w)
            + "\n計画建築物:\n" + "\n".join(f"- {fmt_box(b)}" for b in plan)
            + "\n道路高さ制限適合建築物（段状）:\n" + "\n".join(f"- {fmt_box(b)}" for b in conf)
            + "\n測定点: " + "、".join(f"P{i+1}(x={px:g}, y={py:g})" for i, (px, py) in enumerate(pts))
            + "（いずれも路面の中心の高さ）。\n"
            "全測定点で計画建築物の天空率が適合建築物の天空率以上なら「適合」、"
            "1点でも下回れば「不適合」。\n"
            "「判定: 適合／不適合」と「最も不利な測定点: P番号」の2つを答えよ。"
            "最も不利な測定点とは、（計画建築物の天空率−適合建築物の天空率）を符号つきで比べて最も小さい点"
            "（下回る点があれば、最も大きく下回る点）をいう。"
        )
        out.append({
            "id": f"sky-cmp-{len(out)+1:03d}", "kind": "sky_compare", "topic": "天空率",
            "question": q, "answer": f"{'適合' if ok else '不適合'} / P{imin+1}",
            "answer_norm": f"cmp:{int(ok)}#{imin+1}",
            "geometry": {"points": pts, "plan": plan, "conforming": conf, "road_width": w},
            "evidence": {"law": "建築基準法施行令", "article": "135の6",
                         "values_pct": [[round(a * 100, 3), round(c * 100, 3)] for a, c in rows]},
        })
    return out


def gen_points(rng: random.Random, n: int = 10) -> list[dict]:
    out = []
    seen = set()
    while len(out) < n:
        w = rng.choice([4, 5, 6, 8, 10, 12, 15])
        L = rng.choice([2, 6, 9, 10, 12.5, 15, 18, 20, 25, 30])
        if (w, L) in seen:
            continue
        seen.add((w, L))
        cnt, gap = measurement_points(L, w)
        q = (
            "建築基準法施行令第135条の9第1項の、道路高さ制限の天空率の測定点について答えよ。\n"
            f"前面道路は直線で幅員{w:g}m、敷地が前面道路に面する部分の長さ（間口）は{L:g}m。"
            "敷地全体が道路高さ制限の適用範囲内で、前面道路は1本のみ。\n"
            "条文の要件を満たす最少の測定点の数と、そのときの隣り合う測定点の間隔（m、小数第2位まで）を答えよ。"
        )
        out.append({
            "id": f"sky-pts-{len(out)+1:03d}", "kind": "sky_points", "topic": "天空率",
            "question": q, "answer": f"{cnt}点・間隔{gap:.2f}m", "answer_norm": f"pts:{cnt}#{gap:.4f}",
            "evidence": {"law": "建築基準法施行令", "article": "135の9",
                         "quote": "前号の位置の間の境界線の延長が当該前面道路の幅員の二分の一を超えるときは、"
                                  "当該位置の間の境界線上に当該前面道路の幅員の二分の一以内の間隔で均等に配置した位置"},
        })
    return out


def main() -> int:
    rng = random.Random(SEED)
    tasks = gen_calc(rng) + gen_compare(rng) + gen_points(rng)
    with open(OUT, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    from collections import Counter
    print(f"wrote {len(tasks)} -> {OUT}", dict(Counter(t["kind"] for t in tasks)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
