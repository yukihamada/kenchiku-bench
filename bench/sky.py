"""天空率（建築基準法施行令第135条の5）の決定論的な計算。

Rs = (As - Ab) / As
  As: 想定半球の水平投影面積（半径1なら π）
  Ab: 建築物を想定半球に投影し、さらに水平面へ正射影した面積

建物は直方体の集まり（各直方体は水平な平面形の矩形 [x0,x1]×[y0,y1]、高さ h）で表す。
高さは測定点（前面道路の路面の中心の高さ）からの高さ。

方位角 φ ごとに、測定点からの水平な半直線が直方体の平面形に最初に入る距離 s を求めると、
その方位で見える建物の最大仰角は atan(h / s)（直方体は上面が水平なので、いちばん近い
点が最も高く見える）。仰角 α の点は半径 cos α の位置に正射影されるので、その方位で
隠れる扇形の面積要素は ½(1 - cos²α) dφ = ½ sin²α dφ。これを φ で積分して Ab を得る。

外部ライブラリなし（numpy なし）。selftest で無限長の壁の解析解と突き合わせる。
"""
from __future__ import annotations

import math

Box = dict  # {"x0","x1","y0","y1","h"}


def _entry_distance(px: float, py: float, dx: float, dy: float, b: Box) -> float | None:
    """測定点から方向 (dx,dy) の水平半直線が矩形に入る距離。当たらなければ None。"""
    t0, t1 = 0.0, math.inf
    for p, d, lo, hi in ((px, dx, b["x0"], b["x1"]), (py, dy, b["y0"], b["y1"])):
        if abs(d) < 1e-15:
            if p < lo or p > hi:
                return None
            continue
        a, c = (lo - p) / d, (hi - p) / d
        if a > c:
            a, c = c, a
        t0, t1 = max(t0, a), min(t1, c)
        if t0 > t1:
            return None
    return t0


def sky_ratio(px: float, py: float, boxes: list[Box], pz: float = 0.0,
              steps: int = 36000) -> float:
    """天空率（0〜1）。steps は方位角の分割数（36000 = 0.01°刻み）。"""
    blocked = 0.0
    dphi = 2 * math.pi / steps
    for i in range(steps):
        phi = (i + 0.5) * dphi
        dx, dy = math.cos(phi), math.sin(phi)
        best = 0.0  # sin²α の最大値
        for b in boxes:
            h = b["h"] - pz
            if h <= 0:
                continue
            s = _entry_distance(px, py, dx, dy, b)
            if s is None:
                continue
            if s <= 1e-12:
                best = 1.0
                break
            s2 = h * h / (h * h + s * s)
            if s2 > best:
                best = s2
        blocked += 0.5 * best * dphi
    return (math.pi - blocked) / math.pi


def sky_ratio_grid(px: float, py: float, boxes: list[Box], n: int = 600) -> float:
    """検算用の別解法: 投影円内の格子点ごとに視線を飛ばして当たるか判定する。"""
    hit = tot = 0
    for i in range(n):
        u = -1 + (i + 0.5) * 2 / n
        for j in range(n):
            v = -1 + (j + 0.5) * 2 / n
            r2 = u * u + v * v
            if r2 >= 1:
                continue
            tot += 1
            r = math.sqrt(r2)
            if r < 1e-12:
                hit += any(b["x0"] <= px <= b["x1"] and b["y0"] <= py <= b["y1"] for b in boxes)
                continue
            tan_alt = math.sqrt(1 - r2) / r
            dx, dy = u / r, v / r
            for b in boxes:
                s = _entry_distance(px, py, dx, dy, b)
                if s is not None and s * tan_alt <= b["h"]:
                    hit += 1
                    break
    return 1 - hit / tot


def infinite_wall_sky(d: float, h: float) -> float:
    """距離 d・高さ h の無限長の壁の天空率（解析解）。
    Ab = ½∫₀^π k²sin²φ/(1+k²sin²φ) dφ = (π/2)(1 - 1/√(1+k²)), k = h/d
    """
    k = h / d
    return 1 - 0.5 * (1 - 1 / math.sqrt(1 + k * k))


def measurement_points(frontage: float, road_width: float) -> tuple[int, float]:
    """令135条の9第1項: 両端の2点＋間を幅員の1/2以内の間隔で均等配置。
    返り値: (最少の測定点数, そのときの間隔 m)"""
    half = road_width / 2
    if frontage <= half + 1e-9:
        return 2, frontage
    seg = math.ceil(frontage / half - 1e-9)
    return seg + 1, frontage / seg
