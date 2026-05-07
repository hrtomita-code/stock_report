"""保有株ブリーフィング・ダッシュボードの生成スクリプト.

`portfolio.csv` と `news.json` を読み込み、yfinance から最新価格を取得し、
ルールベースの分類（売却検討 / 様子見 / 継続保有）を付して `index.html` を出力する.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---- 分類の閾値（README にも明記）----
LOSS_SELL_THRESHOLD_PCT = -15.0   # 損益率がこれ以下なら 🔴 売却検討
LOSS_WATCH_THRESHOLD_PCT = 0.0    # -15% < pct ≤ 0% なら 🟡 様子見

# 重大ネガティブ（不祥事・粉飾級）: 損益率にかかわらず 🔴 売却検討に昇格
SEVERE_NEGATIVE_KEYWORDS = (
    "不正", "架空", "粉飾", "不適切取引", "違反", "経営悪化", "巨額損失",
)
# 注意ネガティブ（業績悪化シグナル）: 損益率が +5% 以下なら一段階引き下げ
MILD_NEGATIVE_KEYWORDS = (
    "下方修正", "減益", "リコール", "業績悪化", "訴訟", "減配", "減産",
)

HERE = Path(__file__).parent
CSV_PATH = HERE / "portfolio.csv"
NEWS_PATH = HERE / "news.json"
OUTPUT_PATH = HERE / "index.html"
JST = timezone(timedelta(hours=9))


def load_portfolio() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                {
                    "code": r["銘柄コード"].strip(),
                    "name": r["銘柄名"].strip(),
                    "shares": int(r["保有株数"]),
                    "cost_price": float(r["取得単価"]),
                    "acquired_at": r["取得日"].strip(),
                }
            )
    return rows


def load_news() -> dict[str, Any]:
    if not NEWS_PATH.exists():
        return {"news": {}, "watchpoints": {}, "price_snapshot": {"prices": {}}}
    with NEWS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def is_news_complete(item: dict[str, Any]) -> bool:
    """4項目（タイトル/出典/公開日/URL）すべて揃っているかを検証."""
    for key in ("title", "source", "date", "url"):
        v = item.get(key)
        if not v or not str(v).strip():
            return False
    return True


def fetch_yfinance(code: str) -> tuple[float | None, float | None, str]:
    """(current, prev_close, source_label) を返す. 取得失敗時は None."""
    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return None, None, "yfinance未導入"
    try:
        ticker = yf.Ticker(f"{code}.T")
        hist = ticker.history(period="5d")
        if hist is None or hist.empty:
            return None, None, "データなし"
        current = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else None
        return current, prev, "yfinance"
    except Exception as exc:
        return None, None, f"取得失敗: {exc.__class__.__name__}"


def resolve_price(code: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """価格情報を辞書で返す. 失敗時はエラー情報を保持."""
    current, prev, label = fetch_yfinance(code)
    if current is not None:
        return {
            "current": current,
            "previous_close": prev,
            "source": label,
            "ok": True,
            "error": None,
        }
    fallback = snapshot.get("prices", {}).get(code)
    if fallback:
        return {
            "current": float(fallback["current"]),
            "previous_close": float(fallback.get("previous_close")) if fallback.get("previous_close") else None,
            "source": f"snapshot ({snapshot.get('as_of', '不明')})",
            "ok": True,
            "error": label,
        }
    return {
        "current": None,
        "previous_close": None,
        "source": label,
        "ok": False,
        "error": label,
    }


def classify(pl_pct: float, news_items: list[dict[str, Any]]) -> dict[str, Any]:
    """損益率とニュースキーワードからルールベースで分類."""
    reasons: list[str] = []
    severe: list[str] = []
    mild: list[str] = []
    for n in news_items:
        text = f"{n.get('title', '')} {n.get('summary', '')}"
        for kw in SEVERE_NEGATIVE_KEYWORDS:
            if kw in text and kw not in severe:
                severe.append(kw)
        for kw in MILD_NEGATIVE_KEYWORDS:
            if kw in text and kw not in mild:
                mild.append(kw)

    pl_label = f"損益率 {pl_pct:+.2f}%"
    reasons.append(pl_label)

    if severe:
        reasons.append("重大ネガティブニュース: " + " / ".join(severe[:3]))
    if mild:
        reasons.append("業績注意キーワード: " + " / ".join(mild[:3]))

    # 1. 損益率が大きく毀損 → 売却検討
    if pl_pct <= LOSS_SELL_THRESHOLD_PCT:
        reasons.append(f"閾値 {LOSS_SELL_THRESHOLD_PCT:.0f}% を下回っています")
        return {"level": "sell", "label": "売却検討", "icon": "🔴", "reasons": reasons}

    # 2. 重大ネガティブニュースは P/L にかかわらず売却検討
    if severe:
        return {"level": "sell", "label": "売却検討", "icon": "🔴", "reasons": reasons}

    # 3. 損益率がマイナス〜微益で注意キーワードあり → 売却検討
    if mild and pl_pct <= LOSS_WATCH_THRESHOLD_PCT:
        return {"level": "sell", "label": "売却検討", "icon": "🔴", "reasons": reasons}

    # 4. 損益マイナス、または注意キーワードあり → 様子見
    if pl_pct <= LOSS_WATCH_THRESHOLD_PCT or mild:
        return {"level": "watch", "label": "様子見", "icon": "🟡", "reasons": reasons}

    # 5. 損益プラス & 懸念ニュースなし → 継続保有
    reasons.append("懸念ニュースなし")
    return {"level": "hold", "label": "継続保有", "icon": "🟢", "reasons": reasons}


def build_card(holding: dict[str, Any], news_db: dict[str, Any]) -> dict[str, Any]:
    code = holding["code"]
    snapshot = news_db.get("price_snapshot", {})
    price_info = resolve_price(code, snapshot)

    raw_news = news_db.get("news", {}).get(code, [])
    news_items = [n for n in raw_news if is_news_complete(n)][:3]
    watchpoints = news_db.get("watchpoints", {}).get(code, [])

    if not price_info["ok"]:
        return {
            **holding,
            "ok": False,
            "error": price_info["error"] or "価格取得失敗",
            "news": news_items,
            "watchpoints": watchpoints,
        }

    current = price_info["current"]
    prev = price_info["previous_close"]
    cost_total = holding["cost_price"] * holding["shares"]
    market_value = current * holding["shares"]
    pl = market_value - cost_total
    pl_pct = (pl / cost_total * 100.0) if cost_total else 0.0
    day_change = (current - prev) if prev else None
    day_change_pct = ((current - prev) / prev * 100.0) if prev else None

    cls = classify(pl_pct, news_items)

    return {
        **holding,
        "ok": True,
        "current_price": round(current, 2),
        "previous_close": round(prev, 2) if prev else None,
        "day_change": round(day_change, 2) if day_change is not None else None,
        "day_change_pct": round(day_change_pct, 4) if day_change_pct is not None else None,
        "cost_total": round(cost_total, 2),
        "market_value": round(market_value, 2),
        "pl": round(pl, 2),
        "pl_pct": round(pl_pct, 4),
        "price_source": price_info["source"],
        "classification": cls,
        "news": news_items,
        "watchpoints": watchpoints,
    }


def build_snapshot() -> dict[str, Any]:
    holdings = load_portfolio()
    news_db = load_news()
    cards = []
    for h in holdings:
        print(f"processing {h['code']} {h['name']} ...", file=sys.stderr)
        card = build_card(h, news_db)
        cards.append(card)
        if card["ok"]:
            print(
                f"  -> ¥{card['current_price']:.2f} ({card['price_source']}) "
                f"P/L {card['pl_pct']:+.2f}% [{card['classification']['label']}]",
                file=sys.stderr,
            )
        else:
            print(f"  !! {card['error']}", file=sys.stderr)

    ok_cards = [c for c in cards if c["ok"]]
    total_cost = sum(c["cost_total"] for c in ok_cards)
    total_value = sum(c["market_value"] for c in ok_cards)
    total_pl = total_value - total_cost
    total_pl_pct = (total_pl / total_cost * 100.0) if total_cost else 0.0

    return {
        "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "thresholds": {
            "sell": LOSS_SELL_THRESHOLD_PCT,
            "watch": LOSS_WATCH_THRESHOLD_PCT,
            "severe_keywords": list(SEVERE_NEGATIVE_KEYWORDS),
            "mild_keywords": list(MILD_NEGATIVE_KEYWORDS),
        },
        "cards": cards,
        "summary": {
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pl": round(total_pl, 2),
            "total_pl_pct": round(total_pl_pct, 4),
            "count": len(cards),
            "ok_count": len(ok_cards),
        },
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>保有株ブリーフィング ダッシュボード</title>
<style>
:root {
  --bg: #f5f6fa;
  --ink: #1f2735;
  --ink-soft: #4a5568;
  --muted: #7b8597;
  --border: #e2e6ee;
  --card: #ffffff;
  --header-bg: #1c2541;
  --header-ink: #f5f6fa;
  --accent: #2f4b7c;
  --green: #1b8a5a;
  --green-soft: #e2f4eb;
  --red: #c0392b;
  --red-soft: #fbe7e3;
  --yellow: #b78100;
  --yellow-soft: #fbf2dc;
  --shadow: 0 4px 14px rgba(28, 37, 65, 0.06);
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic UI", "Meiryo", "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 1280px; margin: 0 auto; padding: 24px 20px 48px; }

header.top {
  background: var(--header-bg);
  color: var(--header-ink);
  padding: 24px 28px;
  border-radius: 14px;
  margin-bottom: 22px;
}
header.top h1 { margin: 0 0 6px; font-size: 20px; letter-spacing: 0.04em; }
header.top .updated { font-size: 12px; opacity: 0.75; }
header.top .disclaimer {
  margin-top: 14px; padding: 10px 14px;
  background: rgba(255,255,255,0.08);
  border-left: 3px solid #f5c518;
  border-radius: 6px;
  font-size: 12px; line-height: 1.5;
}
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px; margin-top: 18px;
}
.kpi {
  background: rgba(255,255,255,0.07);
  padding: 12px 16px;
  border-radius: 10px;
}
.kpi .label { font-size: 11px; opacity: 0.75; letter-spacing: 0.08em; }
.kpi .value { font-size: 22px; font-weight: 700; margin-top: 4px; }
.kpi .sub { font-size: 13px; margin-top: 2px; opacity: 0.85; }
.pos { color: #6fe7b3; }
.neg { color: #ffb4ab; }

.toolbar {
  background: var(--card);
  padding: 14px 18px;
  border-radius: 12px;
  box-shadow: var(--shadow);
  margin-bottom: 18px;
  display: flex; flex-wrap: wrap; gap: 18px; align-items: center;
}
.toolbar-group { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.toolbar label { font-size: 12px; color: var(--muted); margin-right: 4px; letter-spacing: 0.04em; }
.toolbar input[type=text] {
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  width: 200px;
  font-family: inherit;
}
.toolbar select {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  background: #fff;
  font-family: inherit;
}
.chk { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; cursor: pointer; user-select: none; }
.chk input { margin: 0; }
.badge-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }
.dot-sell { background: var(--red); }
.dot-watch { background: var(--yellow); }
.dot-hold { background: var(--green); }

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}
.card {
  background: var(--card);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 18px 20px 16px;
  display: flex; flex-direction: column;
  border-top: 4px solid transparent;
}
.card.cls-sell  { border-top-color: var(--red); }
.card.cls-watch { border-top-color: var(--yellow); }
.card.cls-hold  { border-top-color: var(--green); }
.card.cls-error { border-top-color: var(--muted); }

.card-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 12px; margin-bottom: 10px;
}
.card-head .name { font-size: 16px; font-weight: 700; color: var(--ink); }
.card-head .code { font-size: 12px; color: var(--muted); letter-spacing: 0.06em; margin-top: 2px; }

.badge {
  font-size: 12px; font-weight: 700;
  padding: 4px 10px; border-radius: 999px;
  white-space: nowrap;
}
.badge.cls-sell  { background: var(--red-soft); color: var(--red); }
.badge.cls-watch { background: var(--yellow-soft); color: var(--yellow); }
.badge.cls-hold  { background: var(--green-soft); color: var(--green); }
.badge.cls-error { background: #eef0f5; color: var(--muted); }

.metrics {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px 14px;
  padding: 12px 0;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.metric .label { font-size: 11px; color: var(--muted); letter-spacing: 0.04em; }
.metric .value { font-size: 14px; font-weight: 600; margin-top: 2px; }
.metric .value.big { font-size: 16px; }

.reasons {
  margin: 12px 0 8px;
  padding: 10px 12px;
  background: #f8fafc;
  border-radius: 8px;
  font-size: 12.5px;
  color: var(--ink-soft);
}
.reasons .label { font-size: 11px; color: var(--muted); letter-spacing: 0.06em; margin-bottom: 4px; }
.reasons ul { margin: 0; padding-left: 18px; }
.reasons li { margin: 2px 0; }

.section-h {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.08em;
  margin: 12px 0 6px;
  text-transform: uppercase;
}
.news-list, .watch-list { margin: 0; padding: 0; list-style: none; }
.news-item {
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 13px;
}
.news-item:last-child { border-bottom: none; }
.news-item .title { font-weight: 500; line-height: 1.4; display: block; }
.news-item .meta {
  font-size: 11px; color: var(--muted); margin-top: 3px;
}
.news-item .summary {
  font-size: 12px; color: var(--ink-soft); margin-top: 4px; line-height: 1.5;
}
.watch-list li {
  font-size: 12.5px;
  padding: 4px 0 4px 18px;
  position: relative;
  color: var(--ink-soft);
}
.watch-list li::before {
  content: "▸";
  position: absolute;
  left: 4px;
  color: var(--accent);
}

.empty-news {
  font-size: 12px;
  color: var(--muted);
  padding: 4px 0;
  font-style: italic;
}
.error-box {
  background: var(--red-soft);
  color: var(--red);
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  margin: 10px 0;
}

footer.bottom {
  margin-top: 32px;
  padding: 16px 20px;
  background: #fff;
  border-radius: 10px;
  font-size: 12px;
  color: var(--muted);
  box-shadow: var(--shadow);
}
footer.bottom strong { color: var(--ink-soft); }

.empty-grid {
  grid-column: 1 / -1;
  text-align: center;
  padding: 28px;
  color: var(--muted);
  background: #fff;
  border-radius: 12px;
  font-size: 14px;
}

@media (max-width: 540px) {
  header.top { padding: 18px 18px; }
  header.top h1 { font-size: 17px; }
  .toolbar { flex-direction: column; align-items: stretch; }
  .toolbar-group { width: 100%; }
  .toolbar input[type=text] { width: 100%; }
}
</style>
</head>
<body>
<div class="container">
  <header class="top">
    <h1>保有株ブリーフィング ダッシュボード</h1>
    <div class="updated">最終更新: <span id="updated-at"></span> ／ ¥は税込み計算なしの単純評価</div>
    <div class="summary">
      <div class="kpi">
        <div class="label">取得原価合計</div>
        <div class="value" id="kpi-cost"></div>
      </div>
      <div class="kpi">
        <div class="label">現在評価額</div>
        <div class="value" id="kpi-value"></div>
      </div>
      <div class="kpi">
        <div class="label">含み損益</div>
        <div class="value" id="kpi-pl"></div>
        <div class="sub" id="kpi-pl-pct"></div>
      </div>
      <div class="kpi">
        <div class="label">分類サマリ</div>
        <div class="value" id="kpi-classes" style="font-size:14px; line-height:1.7"></div>
      </div>
    </div>
    <div class="disclaimer">
      <strong>免責事項:</strong> 本ツールは個人の判断補助を目的としたダッシュボードであり、投資推奨ではありません。
      表示される分類はあくまで機械的なルール（損益率の閾値とキーワードマッチ）に基づくものであり、最終的な投資判断はご自身の責任で行ってください。
      価格・ニュースは取得時点のものであり、最新の市場状況を反映していない場合があります。
    </div>
  </header>

  <div class="toolbar">
    <div class="toolbar-group">
      <label>分類:</label>
      <label class="chk"><input type="checkbox" data-cls="sell" checked><span class="badge-dot dot-sell"></span>売却検討</label>
      <label class="chk"><input type="checkbox" data-cls="watch" checked><span class="badge-dot dot-watch"></span>様子見</label>
      <label class="chk"><input type="checkbox" data-cls="hold" checked><span class="badge-dot dot-hold"></span>継続保有</label>
    </div>
    <div class="toolbar-group">
      <label>銘柄検索:</label>
      <input type="text" id="search" placeholder="銘柄名・コード">
    </div>
    <div class="toolbar-group">
      <label>並び順:</label>
      <select id="sort">
        <option value="pl_pct_desc">損益率（降順）</option>
        <option value="pl_pct_asc">損益率（昇順）</option>
        <option value="current_desc">現在価格（降順）</option>
        <option value="current_asc">現在価格（昇順）</option>
        <option value="value_desc">評価額（降順）</option>
        <option value="value_asc">評価額（昇順）</option>
        <option value="code_asc">銘柄コード順</option>
      </select>
    </div>
  </div>

  <div class="cards" id="cards"></div>

  <footer class="bottom">
    <strong>免責:</strong> 本ダッシュボードは情報提供のみを目的としており、特定の銘柄の売買を推奨するものではありません。
    投資判断はご自身の責任において行ってください。データはYahoo!ファイナンス、株探、Bloomberg、日本経済新聞、各社IR等を参照しています。
    分類閾値: 売却検討 ≦ <span id="th-sell"></span>%、様子見 ≦ <span id="th-watch"></span>%。
  </footer>
</div>

<script>
const DATA = __DATA__;

const yen = (n) => "¥" + Math.round(n).toLocaleString("ja-JP");
const yenSigned = (n) => (n >= 0 ? "+" : "-") + "¥" + Math.round(Math.abs(n)).toLocaleString("ja-JP");
const pct = (n) => (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
const fmtPrice = (n) => "¥" + n.toLocaleString("ja-JP", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;",
}[c]));

const state = {
  show: { sell: true, watch: true, hold: true, error: true },
  search: "",
  sort: "pl_pct_desc",
};

document.getElementById("updated-at").textContent = DATA.updated_at;
document.getElementById("th-sell").textContent = DATA.thresholds.sell;
document.getElementById("th-watch").textContent = DATA.thresholds.watch;

function renderSummary() {
  const s = DATA.summary;
  document.getElementById("kpi-cost").textContent = yen(s.total_cost);
  document.getElementById("kpi-value").textContent = yen(s.total_value);
  const plEl = document.getElementById("kpi-pl");
  const pctEl = document.getElementById("kpi-pl-pct");
  plEl.textContent = yenSigned(s.total_pl);
  pctEl.textContent = pct(s.total_pl_pct);
  const cls = s.total_pl >= 0 ? "pos" : "neg";
  plEl.classList.add(cls);
  pctEl.classList.add(cls);

  const counts = { sell: 0, watch: 0, hold: 0, error: 0 };
  DATA.cards.forEach((c) => {
    if (!c.ok) counts.error++;
    else counts[c.classification.level]++;
  });
  document.getElementById("kpi-classes").innerHTML =
    `<span style="color:#ffb4ab">🔴 ${counts.sell}</span> / ` +
    `<span style="color:#f5c518">🟡 ${counts.watch}</span> / ` +
    `<span style="color:#6fe7b3">🟢 ${counts.hold}</span>` +
    (counts.error ? ` / <span style="opacity:0.7">⚠ ${counts.error}</span>` : "");
}

function getFilteredSorted() {
  const q = state.search.trim().toLowerCase();
  let rows = DATA.cards.filter((c) => {
    const lvl = c.ok ? c.classification.level : "error";
    if (!state.show[lvl]) return false;
    if (q) {
      const hay = (c.name + " " + c.code).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const cmp = (a, b, key, dir) => {
    const av = a[key] ?? -Infinity;
    const bv = b[key] ?? -Infinity;
    return (av - bv) * dir;
  };
  switch (state.sort) {
    case "pl_pct_desc":  rows.sort((a,b) => cmp(a,b,"pl_pct",-1)); break;
    case "pl_pct_asc":   rows.sort((a,b) => cmp(a,b,"pl_pct", 1)); break;
    case "current_desc": rows.sort((a,b) => cmp(a,b,"current_price",-1)); break;
    case "current_asc":  rows.sort((a,b) => cmp(a,b,"current_price", 1)); break;
    case "value_desc":   rows.sort((a,b) => cmp(a,b,"market_value",-1)); break;
    case "value_asc":    rows.sort((a,b) => cmp(a,b,"market_value", 1)); break;
    case "code_asc":     rows.sort((a,b) => String(a.code).localeCompare(b.code)); break;
  }
  return rows;
}

function newsHtml(news) {
  if (!news || news.length === 0) {
    return '<div class="empty-news">直近の信頼できるニュースは見つかりませんでした。</div>';
  }
  return '<ul class="news-list">' + news.map((n) => `
    <li class="news-item">
      <a class="title" href="${escapeHtml(n.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(n.title)}</a>
      <div class="meta">${escapeHtml(n.source)} ／ ${escapeHtml(n.date)}</div>
      ${n.summary ? `<div class="summary">${escapeHtml(n.summary)}</div>` : ""}
    </li>`).join("") + "</ul>";
}

function watchHtml(items) {
  if (!items || items.length === 0) return "";
  return '<ul class="watch-list">' + items.map((w) => `<li>${escapeHtml(w)}</li>`).join("") + "</ul>";
}

function cardHtml(c) {
  if (!c.ok) {
    return `
      <div class="card cls-error">
        <div class="card-head">
          <div>
            <div class="name">${escapeHtml(c.name)}</div>
            <div class="code">${escapeHtml(c.code)} ／ ${escapeHtml(c.acquired_at)}取得</div>
          </div>
          <span class="badge cls-error">取得失敗</span>
        </div>
        <div class="error-box">価格データを取得できませんでした: ${escapeHtml(c.error || "原因不明")}</div>
        <div class="metrics">
          <div class="metric"><div class="label">保有株数</div><div class="value">${c.shares.toLocaleString("ja-JP")} 株</div></div>
          <div class="metric"><div class="label">取得単価</div><div class="value">${fmtPrice(c.cost_price)}</div></div>
        </div>
        <div class="section-h">関連ニュース</div>
        ${newsHtml(c.news)}
        ${c.watchpoints && c.watchpoints.length ? `<div class="section-h">観察ポイント</div>${watchHtml(c.watchpoints)}` : ""}
      </div>`;
  }
  const lvl = c.classification.level;
  const plCls = c.pl >= 0 ? "pos" : "neg";
  const dayCls = c.day_change > 0 ? "pos" : c.day_change < 0 ? "neg" : "";
  const dayChangeStr = c.day_change == null
    ? "-"
    : `${yenSigned(c.day_change)} (${pct(c.day_change_pct)})`;
  return `
    <div class="card cls-${lvl}">
      <div class="card-head">
        <div>
          <div class="name">${escapeHtml(c.name)}</div>
          <div class="code">${escapeHtml(c.code)} ／ ${escapeHtml(c.acquired_at)}取得</div>
        </div>
        <span class="badge cls-${lvl}">${c.classification.icon} ${escapeHtml(c.classification.label)}</span>
      </div>
      <div class="metrics">
        <div class="metric"><div class="label">現在価格</div><div class="value big">${fmtPrice(c.current_price)}</div></div>
        <div class="metric"><div class="label">前日比</div><div class="value ${dayCls}">${dayChangeStr}</div></div>
        <div class="metric"><div class="label">保有株数 / 取得単価</div><div class="value">${c.shares.toLocaleString("ja-JP")}株 / ${fmtPrice(c.cost_price)}</div></div>
        <div class="metric"><div class="label">評価額</div><div class="value">${yen(c.market_value)}</div></div>
        <div class="metric"><div class="label">含み損益</div><div class="value big ${plCls}">${yenSigned(c.pl)}</div></div>
        <div class="metric"><div class="label">含み損益率</div><div class="value ${plCls}">${pct(c.pl_pct)}</div></div>
      </div>
      <div class="reasons">
        <div class="label">分類根拠</div>
        <ul>${c.classification.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>
      </div>
      <div class="section-h">直近のニュース（最大3件）</div>
      ${newsHtml(c.news)}
      ${c.watchpoints && c.watchpoints.length ? `<div class="section-h">観察ポイント</div>${watchHtml(c.watchpoints)}` : ""}
      <div style="margin-top:auto; font-size:11px; color:var(--muted); padding-top:10px">価格ソース: ${escapeHtml(c.price_source)}</div>
    </div>`;
}

function render() {
  const rows = getFilteredSorted();
  const root = document.getElementById("cards");
  if (rows.length === 0) {
    root.innerHTML = '<div class="empty-grid">条件に該当する銘柄がありません</div>';
  } else {
    root.innerHTML = rows.map(cardHtml).join("");
  }
}

document.querySelectorAll('.toolbar input[type=checkbox]').forEach((cb) => {
  cb.addEventListener("change", () => {
    state.show[cb.dataset.cls] = cb.checked;
    render();
  });
});
document.getElementById("search").addEventListener("input", (e) => {
  state.search = e.target.value;
  render();
});
document.getElementById("sort").addEventListener("change", (e) => {
  state.sort = e.target.value;
  render();
});

renderSummary();
render();
</script>
</body>
</html>
"""


def main() -> None:
    snapshot = build_snapshot()
    payload = json.dumps(snapshot, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__DATA__", payload)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    s = snapshot["summary"]
    print(f"\nwrote {OUTPUT_PATH} ({len(html):,} bytes)", file=sys.stderr)
    print(f"  cost  : ¥{s['total_cost']:,.0f}", file=sys.stderr)
    print(f"  value : ¥{s['total_value']:,.0f}", file=sys.stderr)
    print(f"  pl    : ¥{s['total_pl']:,.0f} ({s['total_pl_pct']:+.2f}%)", file=sys.stderr)
    print(f"  ok    : {s['ok_count']}/{s['count']} 銘柄", file=sys.stderr)


if __name__ == "__main__":
    main()
