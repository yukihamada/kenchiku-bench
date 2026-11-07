#!/usr/bin/env python3
"""エンジン採点をまとめて results/engine/summary.json に保存する。

前提: engine/ で cargo build --bin far_check --bin bcr_check --bin app_check 済み。
天空率は results/sky-crosscheck/rust_tateru_engine.tsv（既存の突き合わせ）を使う。
"""
import json, subprocess, os, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
OUT = os.path.join(ROOT, "results", "engine")


def run_bin(name, tasks_path):
    with open(tasks_path) as f:
        data = f.read()
    r = subprocess.run([os.path.join(ENGINE, "target/debug", name)],
                       input=data, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{name} failed: {r.stderr}")
    return r.stdout.strip().splitlines()


def dump_tasks(kind, path):
    rows = []
    for l in open(os.path.join(ROOT, "data", "tasks.jsonl")):
        t = json.loads(l)
        if t["kind"] != kind:
            continue
        c = t["evidence"]["calc"]
        if kind == "calc_far":
            rows.append({"id": t["id"], "zone": c["zone"], "planned": c["planned"],
                         "road": c["road_width"], "norm": t["answer_norm"]})
        elif kind == "calc_bcr":
            rows.append({"id": t["id"], "zone": c["zone"], "planned": c["planned"],
                         "corner": c["corner"], "fireproof": c["fireproof"],
                         "quasi_fireproof": c.get("quasi_fireproof", False),
                         "in_fire": c["in_fire_zone"],
                         "in_quasi": c["in_quasi_fire_zone"],
                         "norm": t["answer_norm"]})
        elif kind == "calc_daylight":
            rows.append({"id": t["id"], "zone": c["zone"], "d": c["d"], "h": c["h"],
                         "faces_road": c["faces_road"], "skylight": c.get("skylight", False),
                         "engawa": c.get("engawa", False), "norm": t["answer_norm"]})
        elif kind == "applied":
            rows.append({"id": t["id"], "zone": c["zone"], "site": c["site"],
                         "planned_far": c["planned_far"], "planned_bcr": c["planned_bcr"],
                         "road": c["road_width"], "norm": t["answer_norm"]})
    p = f"/tmp/{kind}_tasks.json"
    json.dump(rows, open(p, "w"), ensure_ascii=False)
    return p, len(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    summary = {"engine": "mu-house houki-engine + tateru_engine",
               "measured": "2026-11-07", "kinds": {}}

    # FAR
    p, n = dump_tasks("calc_far", None)
    lines = run_bin("far_check", p)
    ok = sum(int(l.split("\t")[3]) for l in lines)
    summary["kinds"]["calc_far"] = {"n": n, "correct": ok, "accuracy": round(ok / n, 4),
                                    "note": "都市計画容積率を rule_override で供給"}

    # BCR
    p, n = dump_tasks("calc_bcr", None)
    lines = run_bin("bcr_check", p)
    ok = sum(int(l.split("\t")[3]) for l in lines)
    miss = [l.split("\t") for l in lines if l.split("\t")[3] == "0"]
    summary["kinds"]["calc_bcr"] = {
        "n": n, "correct": ok, "accuracy": round(ok / n, 4),
        "note": "法53条3項の角地・防火/準防火地域+10%緩和を実装。",
        "mismatch_ids": [m[0] for m in miss]}

    # applied
    p, n = dump_tasks("applied", None)
    lines = run_bin("app_check", p)
    ok = sum(int(l.split("\t")[5]) for l in lines)
    summary["kinds"]["applied"] = {"n": n, "correct": ok, "accuracy": round(ok / n, 4),
                                   "note": "敷地×限度率。FAR/BCRの限度値から算出"}

    # daylight (令20条 採光補正係数)
    p, n_day = dump_tasks("calc_daylight", None)
    lines = run_bin("daylight_check", p)
    ok_day = sum(int(l.split("\t")[3]) for l in lines)
    miss_day = [l.split("\t") for l in lines if l.split("\t")[3] == "0"]
    summary["kinds"]["calc_daylight"] = {
        "n": n_day, "correct": ok_day, "accuracy": round(ok_day / n_day, 4),
        "note": "令20条2項の採光補正係数(D/H・道路面・天窓・縁側)を実装。",
        "mismatch_ids": [m[0] for m in miss_day]}

    # sky crosscheck
    tsv = os.path.join(ROOT, "results", "sky-crosscheck", "rust_tateru_engine.tsv")
    if os.path.exists(tsv):
        diffs = []
        for l in open(tsv):
            if not l.strip():
                continue
            r = l.split("\t")
            diffs.append(abs(float(r[1]) - float(r[2])))
        summary["kinds"]["sky_crosscheck"] = {
            "n": len(diffs),
            "max_diff_pt": round(max(diffs), 4),
            "mean_diff_pt": round(statistics.mean(diffs), 4),
            "median_diff_pt": round(statistics.median(diffs), 4),
            "within_0_3pt": sum(1 for d in diffs if d <= 0.3),
            "within_0_01pt": sum(1 for d in diffs if d <= 0.01),
            "note": "Python参照実装 vs Rust tateru_engine。±0.3pt以内を合格とする"}

    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
