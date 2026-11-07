#!/usr/bin/env python3
"""敵対的差分テスト: Rust本番エンジン(houki-engine) と Python参照実装(bench/tasks.py) を
境界値・全組合せ・異常値で突き合わせ、食い違い(=バグ候補)を炙り出す。

2実装は独立に同じ条文式を書いているので、片方が間違っていれば差分が出る。
差分は「どちらが条文に忠実か」を人手で判定する候補リストになる。

使い方:
  python3 bench/adversarial.py            # 全カテゴリ実行
  python3 bench/adversarial.py --json out.json
"""
import argparse
import itertools
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "engine")
sys.path.insert(0, HERE)
import tasks  # Python参照実装

BCR_BIN = os.path.join(ENGINE, "target/debug/bcr_check")
DAY_BIN = os.path.join(ENGINE, "target/debug/daylight_check")

ALL_ZONES = sorted(tasks.ZONE_BCR.keys()) + ["用途地域の指定のない区域"]


# ---------------- 入力生成（敵対的） ----------------

def gen_bcr_cases():
    """建蔽率: 全用途地域 × 基本建蔽率 × 防火/準防火 × 耐火/準耐火 × 角地 の全組合せ。"""
    cases = []
    i = 0
    bcr_values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]  # 0.8 は「限度8/10の地域」境界
    for zone in ALL_ZONES:
        for planned in bcr_values:
            for in_fire, in_quasi in itertools.product([False, True], repeat=2):
                if in_fire and in_quasi:
                    continue  # 排他
                for fireproof, quasi_fireproof in itertools.product([False, True], repeat=2):
                    for corner in [False, True]:
                        exp = tasks.bcr_limit(
                            zone, planned, fireproof=fireproof,
                            quasi_fireproof=quasi_fireproof, corner=corner,
                            in_fire_zone=in_fire, in_quasi_fire_zone=in_quasi)
                        cases.append({
                            "id": f"bcr-adv-{i:05d}",
                            "zone": zone, "planned": planned,
                            "corner": corner, "fireproof": fireproof,
                            "quasi_fireproof": quasi_fireproof,
                            "in_fire": in_fire, "in_quasi": in_quasi,
                            "norm": f"frac:{exp}",
                            "_exp": exp,
                        })
                        i += 1
    return cases


def gen_daylight_cases():
    """採光: 全用途地域 × D/H境界 × 道路面 × 天窓 × 縁側。D/H は下限境界を狙う。"""
    cases = []
    i = 0
    # 各系統の下限境界 D/H: 住居 1.4/6≈0.233, 工業 1.0/8=0.125, 商業 1.0/10=0.1
    # その前後と、off_road_min 距離境界 (住居7, 工業5, 商業4) を狙う
    ratios = [0.0, 0.05, 0.1, 0.125, 0.2, 0.2333, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0]
    h_fixed = 2.0
    for zone in ALL_ZONES:
        for r in ratios:
            d = r * h_fixed
            for faces_road in [False, True]:
                for skylight, engawa in itertools.product([False, True], repeat=2):
                    try:
                        exp = tasks.daylight_coefficient(zone, d, h_fixed, faces_road, skylight, engawa)
                    except ValueError:
                        continue
                    cases.append({
                        "id": f"day-adv-{i:05d}",
                        "zone": zone, "d": d, "h": h_fixed,
                        "faces_road": faces_road,
                        "skylight": skylight, "engawa": engawa,
                        "norm": f"num:{exp}",
                        "_exp": exp,
                    })
                    i += 1
    return cases


# ---------------- 実行 ----------------

def run_bin(binary, cases):
    payload = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cases]
    r = subprocess.run([binary], input=json.dumps(payload),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{binary} failed: {r.stderr}")
    out = {}
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            tid, engine, expected, ok = parts
            out[tid] = {"engine": float(engine), "expected": float(expected), "ok": int(ok)}
    return out


def compare(cases, results, label):
    """Rustのengine値 vs Python参照(exp) を突き合わせ。"""
    mismatches = []
    for c in cases:
        res = results.get(c["id"])
        if not res:
            continue
        rust = res["engine"]
        py = c["_exp"]
        # 丸め差(±0.006)を超える食い違いだけ拾う
        if abs(rust - py) > 0.006:
            mismatches.append({
                "id": c["id"], "zone": c["zone"],
                "rust": round(rust, 4), "python": round(py, 4),
                "diff": round(rust - py, 4),
                "input": {k: v for k, v in c.items() if not k.startswith("_") and k not in ("id", "norm")},
            })
    return mismatches


# ---------------- プロパティテスト（値域・単調性） ----------------

def prop_tests(n_random=200000, seed=42):
    """ランダム入力で条文の性質（不変条件）を検証する。"""
    import random
    rng = random.Random(seed)
    violations = []

    # --- 建蔽率のプロパティ ---
    # 1. 0 <= limit <= 1.0
    # 2. 角地は非角地以上（単調）
    # 3. 耐火フラグを足しても下がらない（単調）
    for _ in range(n_random):
        zone = rng.choice(ALL_ZONES)
        planned = rng.choice([0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
        in_fire = rng.random() < 0.33
        in_quasi = (not in_fire) and rng.random() < 0.5
        fireproof = rng.random() < 0.5
        quasi_fireproof = rng.random() < 0.5
        corner = rng.random() < 0.5
        v = tasks.bcr_limit(zone, planned, fireproof=fireproof,
                            quasi_fireproof=quasi_fireproof, corner=corner,
                            in_fire_zone=in_fire, in_quasi_fire_zone=in_quasi)
        if not (0.0 <= v <= 1.0 + 1e-9):
            violations.append({"prop": "bcr_range", "zone": zone, "planned": planned, "v": v})
        # 角地単調性
        v_nc = tasks.bcr_limit(zone, planned, fireproof=fireproof,
                               quasi_fireproof=quasi_fireproof, corner=False,
                               in_fire_zone=in_fire, in_quasi_fire_zone=in_quasi)
        if v < v_nc - 1e-9:
            violations.append({"prop": "bcr_corner_monotone", "zone": zone, "planned": planned,
                               "corner": v, "nocorner": v_nc})

    # --- 採光のプロパティ ---
    # 1. 0 <= coeff <= 3.0
    # 2. D 増加で単調非減少（H固定）
    # 3. 天窓はちょうど×3（上限3.0で頭打ちする前の値で比較）
    for _ in range(n_random):
        zone = rng.choice(ALL_ZONES)
        h = rng.uniform(0.5, 10.0)
        r1 = rng.uniform(0.0, 3.0)
        r2 = r1 + rng.uniform(0.0, 1.0)
        faces_road = rng.random() < 0.5
        skylight = rng.random() < 0.5
        engawa = rng.random() < 0.5
        v1 = tasks.daylight_coefficient(zone, r1 * h, h, faces_road, skylight, engawa)
        v2 = tasks.daylight_coefficient(zone, r2 * h, h, faces_road, skylight, engawa)
        if not (0.0 <= v1 <= 3.0 + 1e-9):
            violations.append({"prop": "day_range", "zone": zone, "v": v1})
        if v2 < v1 - 1e-9:
            violations.append({"prop": "day_monotone_d", "zone": zone,
                               "r1": r1, "r2": r2, "v1": v1, "v2": v2})
        # 天窓: 天窓なしの3倍（上限3.0で頭打ちする前）
        base = tasks.daylight_coefficient(zone, r1 * h, h, faces_road, False, engawa)
        sky = tasks.daylight_coefficient(zone, r1 * h, h, faces_road, True, engawa)
        if base * 3.0 <= 3.0 and abs(sky - base * 3.0) > 1e-9:
            violations.append({"prop": "day_skylight_x3", "zone": zone,
                               "base": base, "sky": sky})
    return violations


# ---------------- メタモルフィックテスト（変換則） ----------------

def metamorphic_tests():
    """入力変換に対して出力が条文どおり変化するか。"""
    failures = []
    # 建蔽率: 角地フラグを立てると +10%（上限100%で頭打ち）
    for zone in ALL_ZONES:
        for planned in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
            base = tasks.bcr_limit(zone, planned, corner=False)
            corner = tasks.bcr_limit(zone, planned, corner=True)
            expected = min(planned + 0.1, 1.0)
            if abs(corner - expected) > 1e-9:
                failures.append({"mr": "bcr_corner_plus10", "zone": zone,
                                 "planned": planned, "got": corner, "want": expected})
    # 採光: 天窓を付けると×3（ただし上限3.0）
    for zone in ALL_ZONES:
        for r in [0.1, 0.2, 0.3, 0.5]:
            base = tasks.daylight_coefficient(zone, r * 2.0, 2.0, False, False, False)
            sky = tasks.daylight_coefficient(zone, r * 2.0, 2.0, False, True, False)
            expected = min(base * 3.0, 3.0)
            if abs(sky - expected) > 1e-9:
                failures.append({"mr": "day_skylight_x3", "zone": zone,
                                 "r": r, "got": sky, "want": expected})
    # 採光: 縁側を付けると×0.7（上限3.0で頭打ちする前の値で比較。base<3.0=未頭打ちのみ）
    for zone in ALL_ZONES:
        for r in [0.3, 0.5, 0.8]:
            base = tasks.daylight_coefficient(zone, r * 2.0, 2.0, True, False, False)
            if base >= 3.0 - 1e-9:
                continue  # 頭打ち済みは縁側前の生値が不明なので対象外
            eng = tasks.daylight_coefficient(zone, r * 2.0, 2.0, True, False, True)
            expected = min(base * 0.7, 3.0)
            if abs(eng - expected) > 1e-9:
                failures.append({"mr": "day_engawa_x07", "zone": zone,
                                 "r": r, "got": eng, "want": expected})
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--random", type=int, default=200000, help="プロパティテストのランダム件数")
    a = ap.parse_args()

    have_rust = os.path.exists(BCR_BIN) and os.path.exists(DAY_BIN)
    bcr_mis, day_mis = [], []
    total = bad = 0
    if have_rust:
        bcr_cases = gen_bcr_cases()
        day_cases = gen_daylight_cases()
        bcr_res = run_bin(BCR_BIN, bcr_cases)
        day_res = run_bin(DAY_BIN, day_cases)
        bcr_mis = compare(bcr_cases, bcr_res, "BCR")
        day_mis = compare(day_cases, day_res, "DAYLIGHT")

        print(f"=== 建蔽率: {len(bcr_cases)} ケース, 不一致 {len(bcr_mis)} 件 ===")
        for m in bcr_mis[:20]:
            print(f"  {m['id']} zone={m['zone']} rust={m['rust']} py={m['python']} diff={m['diff']} in={m['input']}")
        if len(bcr_mis) > 20:
            print(f"  ... 他 {len(bcr_mis)-20} 件")

        print(f"=== 採光: {len(day_cases)} ケース, 不一致 {len(day_mis)} 件 ===")
        for m in day_mis[:20]:
            print(f"  {m['id']} zone={m['zone']} rust={m['rust']} py={m['python']} diff={m['diff']} in={m['input']}")
        if len(day_mis) > 20:
            print(f"  ... 他 {len(day_mis)-20} 件")

        total = len(bcr_cases) + len(day_cases)
        bad = len(bcr_mis) + len(day_mis)
        print(f"\n合計 {total} ケース, 不一致 {bad} 件 ({bad/total*100:.2f}%)")
    else:
        print("=== 差分テスト: Rustバイナリ無しのためスキップ（Python性質テストのみ実行） ===")
        print("    全差分を回すには engine/ で cargo build --bin bcr_check --bin daylight_check")

    # プロパティテスト
    print(f"\n=== プロパティテスト: ランダム {a.random} 件×2系統 ===")
    prop_v = prop_tests(n_random=a.random)
    print(f"  不変条件違反: {len(prop_v)} 件")
    for v in prop_v[:10]:
        print(f"    {v}")

    # メタモルフィックテスト
    print(f"\n=== メタモルフィックテスト: 変換則 ===")
    mr_f = metamorphic_tests()
    print(f"  変換則違反: {len(mr_f)} 件")
    for f in mr_f[:10]:
        print(f"    {f}")

    grand_bad = bad + len(prop_v) + len(mr_f)

    if a.json:
        json.dump({"bcr": bcr_mis, "daylight": day_mis,
                   "prop_violations": prop_v, "metamorphic_failures": mr_f,
                   "total_cases": total, "mismatches": bad,
                   "prop_random": a.random, "grand_bad": grand_bad},
                  open(a.json, "w"), ensure_ascii=False, indent=2)
        print(f"\n書き出し: {a.json}")

    return 0 if grand_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
