"""天空率の追加タスク（data/tasks_sky2.jsonl）。地盤の高低差と、前面道路が2本ある角地。

kind:
  calc_sky_grade   敷地の地盤が前面道路より高い場合の天空率（令135条の9第4項・令135条の5）
                   測定点は (高低差-1m)/2 だけ高い位置とみなし、敷地の地盤そのものも天空を遮る。
  sky_points_2road 角地（前面道路2本）の道路高さ制限の測定点の数（令132条1項・3項、令135条の9第3項）
                   狭い道路側は、広い道路の境界線から 幅員×2 かつ 35m 以内が「広い道路と同じ幅員」の区域、
                   その先は狭い道路のみの区域。測定点は区域ごとに両端＋幅員の1/2以内の間隔。
正解はすべて bench/sky.py の計算から。既存の50問（tasks_sky.jsonl）とは別ファイル。
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from sky import measurement_points, sky_ratio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "tasks_sky2.jsonl")
SEED = 1359


def deemed_point_height(g: float) -> float:
    """令135条の9第4項: 地盤面が路面の中心より1m以上高いとき、(高低差-1)/2 だけ高い位置とみなす。"""
    return (g - 1) / 2 if g >= 1 else 0.0


def gen_grade(rng: random.Random, n: int = 10) -> list[dict]:
    out = []
    gs = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 2.0, 3.0]
    while len(out) < n:
        g = gs[len(out)]
        w = rng.choice([4, 5, 6, 8])
        sw, sd = rng.choice([12, 15, 18]), rng.choice([15, 18, 20])  # 敷地の間口・奥行
        bx0 = rng.choice([1, 2]); bw = sw - bx0 - rng.choice([1, 2])
        by0 = rng.choice([1, 2, 3]); bd = rng.choice([8, 10, 12])
        h = rng.choice([7, 9, 10, 12])  # 敷地の地盤面からの高さ
        px = rng.choice([0, sw / 2, sw])
        pz = deemed_point_height(g)
        boxes = [dict(x0=0, x1=sw, y0=0, y1=sd, h=g),  # 敷地の地盤（路面より g 高い）
                 dict(x0=bx0, x1=bx0 + bw, y0=by0, y1=by0 + bd, h=g + h)]
        rs = sky_ratio(px, -w, boxes, pz=pz)
        q = (
            "建築基準法施行令第135条の5・第135条の9の天空率を計算せよ。\n"
            f"座標系（単位m）: x軸は前面道路に沿う方向、y軸は道路から敷地の奥へ向かう方向。"
            f"前面道路は y=-{w}〜0（幅員{w}m、平坦）。\n"
            f"敷地は x=0〜{sw}, y=0〜{sd} の長方形で、敷地の地盤面は全面平坦、前面道路の路面の中心より {g:g}m 高い（道路境界で垂直に立ち上がる）。"
            "敷地の外は路面の中心と同じ高さで平坦。建物と敷地の地盤以外に天空を遮るものはない。\n"
            f"建築物: x={bx0}〜{bx0 + bw}, y={by0}〜{by0 + bd}、敷地の地盤面からの高さ{h}m の直方体。\n"
            f"測定点: 前面道路の反対側の境界線上の x={px:g}（令第135条の9に従い高さを定めること）。\n"
            "天空率をパーセントで小数第1位まで答えよ。最後の行は「ANSWER: 72.4%」の形にせよ。"
        )
        out.append({
            "id": f"sky2-grade-{len(out)+1:03d}", "kind": "calc_sky_grade", "topic": "天空率",
            "question": q, "answer": f"{rs*100:.1f}%", "answer_norm": f"pct:{rs*100:.4f}",
            "geometry": {"point": [px, -w], "z": pz, "boxes": boxes, "road_width": w, "ground": g},
            "evidence": {"law": "建築基準法施行令", "article": "135の9",
                         "calc": f"測定点の高さ={pz:g}m（(高低差-1)/2、1m未満は0）・敷地の地盤も遮蔽物として算入（令135条の5のAb）"},
        })
    return out


def gen_two_roads(rng: random.Random, n: int = 10) -> list[dict]:
    out = []
    seen = set()
    while len(out) < n:
        a1 = rng.choice([6, 8, 10, 12, 15, 20])   # 広い道路
        a2 = rng.choice([4, 4.5, 5, 6])           # 狭い道路
        if a2 >= a1:
            continue
        l1 = rng.choice([10, 12, 15, 20])         # 広い道路に面する長さ
        l2 = rng.choice([12, 15, 20, 25, 30, 40, 45])  # 狭い道路に面する長さ
        if (a1, a2, l1, l2) in seen:
            continue
        seen.add((a1, a2, l1, l2))
        d = min(2 * a1, 35)
        n1, _ = measurement_points(l1, a1)
        la = min(l2, d)
        n2a, _ = measurement_points(la, a1)
        n2b = measurement_points(l2 - d, a2)[0] if l2 > d else 0
        total = n1 + n2a + n2b
        q = (
            "建築基準法施行令第132条・第135条の9による、道路高さ制限の天空率の測定点の数を求めよ。\n"
            f"角地で、前面道路は2本。道路Aは幅員{a1:g}m、道路Bは幅員{a2:g}m（どちらも直線・平坦、反対側に公園等はない）。"
            f"敷地は長方形で、道路Aに面する長さ{l1:g}m、道路Bに面する長さ{l2:g}m（2本の道路は敷地の角で直交）。"
            "敷地全体が道路高さ制限の適用範囲内。道路Bの中心線から10mを超える区域は道路Bに面していない。\n"
            "条文の要件を満たす最少の測定点の数を、(1)道路A側、(2)道路B側のうち道路Aと同じ幅員とみなす区域、"
            "(3)道路B側のその他の区域（なければ0）に分けて求め、合計も示せ。\n"
            "最後の行は「ANSWER: 合計,(1),(2),(3)」の形（例: ANSWER: 12,5,4,3）にせよ。"
        )
        out.append({
            "id": f"sky2-2road-{len(out)+1:03d}", "kind": "sky_points_2road", "topic": "天空率",
            "question": q, "answer": f"{total},{n1},{n2a},{n2b}", "answer_norm": f"tuple:{total},{n1},{n2a},{n2b}",
            "evidence": {"law": "建築基準法施行令", "article": "132",
                         "calc": f"道路B側の区域={la:g}m（道路Aの境界から min(2×{a1:g}, 35)={d:g}m 以内）→幅員{a1:g}mとみなす、"
                                 f"残り{max(l2 - d, 0):g}m→幅員{a2:g}m。各区域で両端＋幅員の1/2以内の均等配置（令135条の9第1項・第3項）"},
        })
    return out


def main() -> int:
    rng = random.Random(SEED)
    tasks = gen_grade(rng) + gen_two_roads(rng)
    with open(OUT, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    from collections import Counter
    print(f"wrote {len(tasks)} -> {OUT}", dict(Counter(t["kind"] for t in tasks)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
