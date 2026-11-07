# kenchiku-bench

日本の建築基準法に特化した LLM の性能を、**正解が機械的に決まる**ベンチマークで測る。

- 対象法令: 建築基準法 / 同施行令 / 同施行規則（e-Gov 法令API から取得、本則 851 条）
- 正解は条文テキストか決定論的計算から生成（LLM に正解を書かせない）
- 各タスクは `evidence` に根拠条文・引用・計算条件を持つので後から監査できる

## 使い方

```bash
export TEAI_API_KEY=...            # https://api.teai.io/v1

python3 corpus/fetch_laws.py       # 条文取得（取得済みなら不要）
python3 bench/tasks.py             # data/tasks.jsonl を生成
python3 bench/selftest.py          # API を叩かない自己テスト

python3 bench/run.py --models openai/gpt-5.6-luna --rag --tag rag
python3 bench/run.py --models openai/gpt-5.6-luna,openai/gpt-5.5 --rag --concurrency 6 --tag rag
python3 bench/regrade.py --tag rag # 採点基準を変えたとき、保存済み回答を再採点
```

結果は `results/<tag>/<model>.jsonl`（1行1タスク）と `summary.json`。
実測のまとめは [REPORT.md](REPORT.md)。

## タスク種別（203件）

| kind | 件数 | 内容 |
|---|---|---|
| numeric | 23 | 条文の数値穴埋め |
| cite | 27 | 見出しから条文を同定 |
| calc_bcr | 30 | 建蔽率の限度（法53条、緩和込み） |
| calc_far | 30 | 容積率の限度（法52条、前面道路制限込み） |
| calc_daylight | 40 | 採光補正係数（令20条） |
| false_claim | 23 | 数値を1つ改変した条文の誤り検出 |
| applied | 30 | 敷地から延べ面積・建築面積の上限を算定（2段計算） |

## 天空率（50問・別ファイル `data/tasks_sky.jsonl`）

天空率は条文を読むだけでは答えが出ない（立体を想定半球に投影した面積が要る）ため、
正解は `bench/sky.py` の計算から作る。方位角0.01°刻みの正射影積分で、無限長の壁の解析解と
小数第6位まで一致、格子による別解法とも一致することを `selftest.py` で確かめている。

| kind | 件数 | 内容 |
|---|---|---|
| calc_sky | 20 | 直方体1〜2棟の天空率（令135条の5、±0.3ポイント） |
| sky_compare | 20 | 計画建築物と道路高さ制限適合建築物を3測定点で比較（令135条の6）。適合判定＋最も不利な測定点 |
| sky_points | 10 | 道路高さ制限の測定点の最少数と間隔（令135条の9） |

```bash
python3 bench/tasks_sky.py   # data/tasks_sky.jsonl を生成
python3 bench/run.py --models openai/gpt-5.6-luna --tasks data/tasks_sky.jsonl --rag --tag sky-rag
python3 bench/run.py --models openai/gpt-5.6-luna --tasks data/tasks_sky.jsonl --rag --tools --tag sky-tools
```

`--tools` はモデルに計算ツール `sky_ratio`（天空率）と `sky_measurement_points`（測定点）を渡す。
ツール定義は `bench/run.py` の `TOOLS`、実体は `bench/sky.py`。自分のアプリで使うときもこの2つをそのまま渡せばよい。

実測（2026-11-03・条文あり・temperature 0・各1回）:

| モデル | ツールなし | ツールあり | 1問あたり（ツールあり） | 中央値（ツールあり） |
|---|---|---|---|---|
| openai/gpt-5.6-luna | 52% | 98% | $0.0035 | 7.7秒 |
| google/gemini-3.5-flash | 34% | 100% | $0.0096 | 7.8秒 |
| openai/gpt-5.4-nano | 16% | 100% | $0.0018 | 5.0秒 |
| openai/gpt-5.5 | 測定できず（再測定でも50問中30問が上流エラー・応答20問中19問正解） | 100% | $0.0343 | 4.0秒 |

対象外: 地盤の高低差（令135条の9第4項）、敷地の一部だけが適用範囲になる場合の切り取り、
斜面状の適合建築物、2以上の前面道路、隣地・北側の天空率。実際の敷地での天空率は
bim.house の `/tochi/tateru`（`mu-house/src/tateru_engine.rs`）が別に計算している。

## RAG

外部依存なしの簡易ハイブリッド検索（BM25 + 見出し完全一致 + 5-gram 包含一致）。
質問文の「第52条」等の明示参照は条文を直接引き、引用文は包含一致で引く。
条文は上限 6000 字まで、渡された順に詰める（段落抜粋は精度が落ちた）。

## エンジン採点（製品の計算そのものを測る）

LLMの回答精度とは別に、bim.house / TSUGI の計算エンジンを同じ正解データで採点できる。
対象は `mu-house` の `crates/houki-engine`（建蔽率・容積率）と `src/tateru_engine.rs`（天空率）。

```bash
# engine/ は mu-house のソースを相対パスで読み込む（隣に mu-house が必要）
cd engine
cargo build --bin far_check --bin bcr_check --bin app_check
cd ..
python3 bench/run_engine.py   # results/engine/summary.json
```

実測（2026-11-07）: 容積率 30/30・面積上限 30/30・天空率 140/140（±0.3pt以内）。
建蔽率は 13/30（法53条3項の角地・防火地域緩和が未実装）。採光は令20条の補正係数が未実装で対象外。
詳細は [REPORT.md](REPORT.md) の「エンジン採点」。
