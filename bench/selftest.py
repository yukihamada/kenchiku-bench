#!/usr/bin/env python3
"""外部APIを叩かない自己テスト。採点・検索・タスク生成の回帰を確認する。

  python3 bench/selftest.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from bench.grade import extract_articles, grade  # noqa: E402
from bench.run import Retriever, build_context, load_articles  # noqa: E402
from bench.tasks import daylight_coefficient, far_limit  # noqa: E402

TASKS = {t["id"]: t for t in (json.loads(l) for l in open(os.path.join(ROOT, "data", "tasks.jsonl"), encoding="utf-8"))}


def check(name: str, cond: bool) -> None:
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def main() -> int:
    # 1. 正解をそのまま入れれば全問正解（採点の取りこぼし検出）
    bad = [tid for tid, t in TASKS.items() if not grade(t, t["answer"])[0]]
    check(f"oracle 全問正解 ({len(TASKS)}件)", not bad)

    # 2. 条番号の表記ゆれ
    check("第56条の2 = 第56の2条", extract_articles("建築基準法第56条の2") == {"56の2"})
    check("漢数字の条番号", extract_articles("第五十六条の二") == {"56の2"})

    # 3. 誤り検出は「正しい数値」の直後だけを見る（無関係な数値で通さない）
    t = TASKS["false-000"]
    check("無関係な数値では不正解", not grade(t, "誤り。五百平方メートル以内です。")[0])
    check("正しい数値+条番号で正解", grade(t, t["answer"])[0])
    check("改行直後の数値も拾う",
          grade(TASKS["false-014"], "誤り。正しい数値：\n- 一階：三メートル以下 根拠：建築基準法第2条")[0])

    # 4. 決定論的計算
    check("採光補正係数 住居系", abs(daylight_coefficient("第一種住居地域", 3.0, 3.0, True) - 3.0) < 1e-9)
    check("容積率 前面道路", far_limit("第一種住居地域", 5.0, 10.0) == 4.0)

    # 5. 検索: 明示参照した条文がコンテキストに入る
    arts = load_articles()
    r = Retriever(arts)
    miss = []
    for t in TASKS.values():
        ev = t["evidence"]
        if "law" not in ev:
            continue
        ctx = build_context(t, r, arts)
        if f"第{ev['article']}条" not in ctx:
            miss.append(t["id"])
    check(f"根拠条文がコンテキストに入る ({len(TASKS)}件中 {len(miss)}件欠落)", not miss)

    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
