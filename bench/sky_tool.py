#!/usr/bin/env python3
"""天空率の計算ツール（1ファイル・外部ライブラリなし）。teai.io/blog/kenchiku-benchmark の追試で使ったもの。

使い方は3通り:

  1) 計算だけ（AIなし）
     python3 sky_tool.py calc --point 0,-6 --box 0,10,2,14,12
       → 測定点 (x=0, y=-6) から見た、x=0〜10・y=2〜14・高さ12m の直方体の天空率

  2) 測定点の数（令135条の9第1項）。--json で各点の座標も出す
     python3 sky_tool.py points --frontage 12 --road 6 [--json]

  3) 計画建築物と道路高さ制限適合建築物の比較（令135条の6第1項第1号）
     python3 sky_tool.py compare --frontage 12 --road 6 --plan 1,11,2,14,15 --conf 0,12,0,6,7.5 --conf 0,12,6,12,15

  4) AIに質問して、計算はこのツールにさせる（teai.io の API キーが必要）
     export TEAI_API_KEY=...
     python3 sky_tool.py ask "幅員6mの道路の反対側 (x=0,y=-6) から、x=0〜10, y=2〜14, 高さ12mの建物の天空率は？"

座標の決め方: x軸=前面道路に沿う方向、y軸=道路から敷地の奥へ。前面道路は y=-幅員〜0、敷地は y≥0。
高さ h と測定点の高さ z は同じ基準面（ふつうは前面道路の路面の中心）から測る。

計算方法: 建築基準法施行令第135条の5 の Rs=(As-Ab)/As を、方位角0.01°刻みの正射影積分で求める。
無限に長い壁の解析解と小数第6位まで一致することを確認済み。
対象は直方体の組み合わせまで。斜線どおりの斜面形・地形の起伏はそのままでは表せない（細かい直方体に分けて近似する）。
確認申請に使う数値は、必ず申請に用いる天空率ソフトで確認すること。

MIT License (c) 2026 teai.io
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request

API = os.environ.get("TEAI_BASE_URL", "https://api.teai.io/v1")
MODEL = os.environ.get("TEAI_MODEL", "openai/gpt-5.6-luna")


# ---------------------------------------------------------------- 計算

def _entry(px, py, dx, dy, b):
    t0, t1 = 0.0, math.inf
    for p, d, lo, hi in ((px, dx, b["x0"], b["x1"]), (py, dy, b["y0"], b["y1"])):
        if abs(d) < 1e-15:
            if p < lo or p > hi:
                return None
            continue
        a, c = sorted(((lo - p) / d, (hi - p) / d))
        t0, t1 = max(t0, a), min(t1, c)
        if t0 > t1:
            return None
    return t0


def sky_ratio(px: float, py: float, boxes: list[dict], pz: float = 0.0, steps: int = 36000) -> float:
    """天空率（0〜1）。boxes = [{"x0","x1","y0","y1","h"}, ...]"""
    blocked, dphi = 0.0, 2 * math.pi / steps
    for i in range(steps):
        phi = (i + 0.5) * dphi
        dx, dy = math.cos(phi), math.sin(phi)
        best = 0.0
        for b in boxes:
            h = b["h"] - pz
            if h <= 0:
                continue
            s = _entry(px, py, dx, dy, b)
            if s is None:
                continue
            if s <= 1e-12:
                best = 1.0
                break
            best = max(best, h * h / (h * h + s * s))
        blocked += 0.5 * best * dphi
    return (math.pi - blocked) / math.pi


def measurement_points(frontage: float, road_width: float) -> tuple[int, float]:
    """令135条の9第1項: 両端＋幅員の1/2以内の間隔で均等配置。(最少の点数, 間隔m)"""
    half = road_width / 2
    if frontage <= half + 1e-9:
        return 2, frontage
    seg = math.ceil(frontage / half - 1e-9)
    return seg + 1, frontage / seg


# ---------------------------------------------------------------- AIに渡すツール定義（OpenAI互換 function calling）

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
            v = sky_ratio(float(args["x"]), float(args["y"]), boxes, float(args.get("z") or 0))
            return json.dumps({"sky_ratio_pct": round(v * 100, 3)})
        if name == "sky_measurement_points":
            n, gap = measurement_points(float(args["frontage"]), float(args["road_width"]))
            return json.dumps({"points": n, "spacing_m": round(gap, 4)})
    except Exception as e:  # 引数の誤りはAIに返して直させる
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    return json.dumps({"error": f"unknown tool {name}"})


def ask(question: str, verbose: bool = True) -> str:
    key = os.environ.get("TEAI_API_KEY")
    if not key:
        sys.exit("TEAI_API_KEY を設定してください（https://teai.io で発行）")
    msgs = [{"role": "system", "content": "あなたは日本の建築基準法に詳しい。天空率や測定点の数値は手計算せず、"
                                          "必ずツールで計算し、その結果と根拠条文を短く示して答える。数式記法（LaTeX）は使わない。"},
            {"role": "user", "content": question}]
    for _ in range(8):
        body = json.dumps({"model": MODEL, "messages": msgs, "tools": TOOLS, "temperature": 0}).encode()
        req = urllib.request.Request(f"{API}/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            msg = json.load(r)["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            args = json.loads(c["function"].get("arguments") or "{}")
            out = call_tool(c["function"]["name"], args)
            if verbose:
                print(f"[ツール] {c['function']['name']}({json.dumps(args, ensure_ascii=False)}) → {out}", file=sys.stderr)
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
    return "（ツール呼び出しが8回を超えたため中断）"


# ---------------------------------------------------------------- コマンドライン

def _nums(s: str) -> list[float]:
    return [float(v) for v in s.split(",")]


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[1], argv[2:]
    if cmd == "calc":
        point, boxes, z = None, [], 0.0
        it = iter(rest)
        for a in it:
            if a == "--point":
                point = _nums(next(it))
            elif a == "--box":
                x0, x1, y0, y1, h = _nums(next(it))
                boxes.append(dict(x0=x0, x1=x1, y0=y0, y1=y1, h=h))
            elif a == "--z":
                z = float(next(it))
        if not point or not boxes:
            sys.exit("例: python3 sky_tool.py calc --point 0,-6 --box 0,10,2,14,12")
        print(f"天空率 {sky_ratio(point[0], point[1], boxes, z) * 100:.3f}%")
        return 0
    if cmd == "points":
        flags = [x for x in rest if x == "--json"]
        rest2 = [x for x in rest if x != "--json"]
        d = dict(zip(rest2[::2], rest2[1::2]))
        frontage, road = float(d["--frontage"]), float(d["--road"])
        n, gap = measurement_points(frontage, road)
        if flags:  # 座標つき（x=0 を間口の片端、反対側の境界線 y=-幅員 上）
            pts = [{"x": round(i * gap, 4), "y": -road, "z": 0} for i in range(n)]
            print(json.dumps({"points": n, "spacing_m": round(gap, 4), "coords": pts}, ensure_ascii=False))
        else:
            print(f"測定点 {n}点・間隔 {gap:.2f}m")
        return 0
    if cmd == "compare":
        # 令135条の6第1項第1号の比較: 全測定点で 計画 ≥ 適合 なら適合
        plan, conf, kv = [], [], {}
        it = iter(rest)
        for a in it:
            if a in ("--plan", "--conf"):
                x0, x1, y0, y1, h = _nums(next(it))
                (plan if a == "--plan" else conf).append(dict(x0=x0, x1=x1, y0=y0, y1=y1, h=h))
            else:
                kv[a] = next(it)
        if not plan or not conf or "--frontage" not in kv or "--road" not in kv:
            sys.exit("例: python3 sky_tool.py compare --frontage 12 --road 6 --plan 1,11,2,14,15 --conf 0,12,0,6,7.5 --conf 0,12,6,12,15")
        frontage, road = float(kv["--frontage"]), float(kv["--road"])
        n, gap = measurement_points(frontage, road)
        worst = None
        print(f"測定点 {n}点（間隔 {gap:.2f}m、反対側の境界線 y=-{road:g}）")
        print("  点   x      計画      適合      差")
        for i in range(n):
            x = i * gap
            a_, c_ = sky_ratio(x, -road, plan) * 100, sky_ratio(x, -road, conf) * 100
            d = a_ - c_
            if worst is None or d < worst[1]:
                worst = (i + 1, d)
            print(f"  P{i+1:<3} {x:6.2f}  {a_:7.3f}%  {c_:7.3f}%  {d:+7.3f}")
        print(f"判定: {'適合' if worst[1] >= 0 else '不適合'}（最も不利な点 P{worst[0]}、差 {worst[1]:+.3f}ポイント）")
        return 0
    if cmd == "ask":
        print(ask(" ".join(rest)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
