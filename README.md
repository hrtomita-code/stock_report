# 保有株ブリーフィング・ダッシュボード

`portfolio.csv` を起点に、保有株の現在価格・前日比・52週レンジ・含み損益・直近ニュース・観察ポイントを並べる **情報整理用の補助ツール** です。GitHub Pages で公開できるよう、外部依存なしの単一 `index.html` + `data.json` という構成にしています。

> **免責**: 本ツールは情報整理のための補助ツールであり、投資推奨ではありません。価格・ニュースには誤りや遅延がありえます。最終判断はご自身でお願いします。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `portfolio.csv` | 保有銘柄マスタ（UTF-8 / `銘柄コード,銘柄名,保有株数,取得単価,取得日`） |
| `fetch.py` | yfinance で価格・分割を取得し `data.json` を生成 |
| `data.json` | ダッシュボードの表示データ（価格 / 損益 / カテゴリ / ニュース / 観察ポイント） |
| `index.html` | フィルタ・ソート付きの単一ページ（外部ライブラリなし、`data.json` を fetch） |
| `requirements.txt` | Python 依存（yfinance, pandas） |

## 再生成手順

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python fetch.py             # data.json を更新
git add data.json
git commit -m "data: refresh prices"
git push
```

ニュースと観察ポイントは現状 `data.json` を直接編集して更新します（4要素 タイトル/出典/日付/URL が揃っていない項目は表示時に自動で除外）。

## カテゴリ判定（透明性のため公開）

説明可能な数値ルールで一意に決定しています。ニュース要因は判定理由の文章に併記しますが、カテゴリそのものは下表のみで決まります。

| カテゴリ | 条件 |
|---|---|
| 売却検討 | 含み損益率 ≦ −20% |
| 様子見 | −20% < 含み損益率 < +5% |
| 継続保有 | 含み損益率 ≧ +5% |

## 株式分割の扱い

`fetch.py` は `yf.Ticker(t).splits` を取得日以降で適用し、保有株数と取得単価を補正します。取得日が `portfolio.csv` に無い銘柄は調整不可として `data.json` の `split_note` に明示し、画面でも「分割調整なし」と注記します。

## ローカルで開く

```bash
python -m http.server 8000   # http://localhost:8000/
```

`file://` で開いても動きますが、ブラウザによっては `fetch("data.json")` が CORS で失敗するため、`http.server` 経由が確実です。
