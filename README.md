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

## RAG

外部依存なしの簡易ハイブリッド検索（BM25 + 見出し完全一致 + 5-gram 包含一致）。
質問文の「第52条」等の明示参照は条文を直接引き、引用文は包含一致で引く。
条文は上限 6000 字まで、渡された順に詰める（段落抜粋は精度が落ちた）。
