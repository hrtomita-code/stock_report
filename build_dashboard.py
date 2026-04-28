"""Build a single-file interactive portfolio dashboard.

Reads portfolio.csv, fetches the latest prices from Yahoo Finance via yfinance,
and writes portfolio_dashboard.html with the data embedded as JSON.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import yfinance as yf  # type: ignore
    _HAS_YF = True
except Exception:  # pragma: no cover
    _HAS_YF = False


HERE = Path(__file__).parent
CSV_PATH = HERE / "portfolio.csv"
OUTPUT_PATH = HERE / "portfolio_dashboard.html"
JST = timezone(timedelta(hours=9))

# Latest close / latest quote retrieved from public sources (Yahoo Finance Japan
# / Kabutan via web search) on 2026-04-28 because yfinance is blocked in this
# sandbox. These are used as a fallback when live fetch fails.
PRICE_FALLBACK: dict[str, float] = {
    "7203": 3067.0,    # トヨタ自動車 (2026-04-27 close)
    "9984": 5844.0,    # ソフトバンクグループ (2026-04-27 close)
    "6758": 3188.0,    # ソニーグループ (2026-04-27 close)
    "8306": 2788.0,    # 三菱UFJフィナンシャル・グループ (2026-04-27 close)
    "9433": 2677.5,    # KDDI (latest quote)
}


def load_portfolio() -> list[dict]:
    rows: list[dict] = []
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


def fetch_latest_price(code: str) -> float | None:
    """Fetch the latest close price for a Japanese stock code via yfinance."""
    if not _HAS_YF:
        return None
    ticker = yf.Ticker(f"{code}.T")
    try:
        hist = ticker.history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as exc:  # pragma: no cover - network dependent
        print(f"  warn: history failed for {code}: {exc}")

    try:
        info = ticker.fast_info
        price = info.get("last_price") if hasattr(info, "get") else getattr(info, "last_price", None)
        if price:
            return float(price)
    except Exception as exc:  # pragma: no cover - network dependent
        print(f"  warn: fast_info failed for {code}: {exc}")

    return None


def resolve_price(code: str) -> tuple[float, str]:
    """Return (price, source_label)."""
    live = fetch_latest_price(code)
    if live is not None:
        return live, "live"
    if code in PRICE_FALLBACK:
        return PRICE_FALLBACK[code], "fallback"
    return 0.0, "missing"


def build_snapshot() -> dict:
    holdings = load_portfolio()
    enriched = []
    for h in holdings:
        print(f"fetching {h['code']} {h['name']} ...")
        price, source = resolve_price(h["code"])
        if source == "missing":
            print(f"  !! no price available for {h['code']}, falling back to cost price")
            price = h["cost_price"]
        else:
            print(f"  -> {price} ({source})")
        cost_total = h["cost_price"] * h["shares"]
        market_value = price * h["shares"]
        pl = market_value - cost_total
        pl_pct = (pl / cost_total * 100.0) if cost_total else 0.0
        enriched.append(
            {
                **h,
                "current_price": round(price, 2),
                "cost_total": round(cost_total, 2),
                "market_value": round(market_value, 2),
                "pl": round(pl, 2),
                "pl_pct": round(pl_pct, 4),
            }
        )

    total_cost = sum(e["cost_total"] for e in enriched)
    total_value = sum(e["market_value"] for e in enriched)
    total_pl = total_value - total_cost
    total_pl_pct = (total_pl / total_cost * 100.0) if total_cost else 0.0

    return {
        "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "holdings": enriched,
        "summary": {
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pl": round(total_pl, 2),
            "total_pl_pct": round(total_pl_pct, 4),
        },
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ポートフォリオ ダッシュボード</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #0f1b2d;
  --bg-soft: #16263f;
  --card: #ffffff;
  --card-2: #f4f7fb;
  --ink: #1a2842;
  --muted: #6b7a90;
  --border: #e3e9f2;
  --accent: #1f3a68;
  --accent-strong: #14274a;
  --green: #1e9e6a;
  --green-soft: #e6f6ee;
  --red: #d64545;
  --red-soft: #fceaea;
  --shadow: 0 6px 18px rgba(15, 27, 45, 0.08);
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: linear-gradient(180deg, #0f1b2d 0%, #142540 280px, #f4f7fb 280px, #f4f7fb 100%);
  color: var(--ink);
  font-family: "Noto Sans JP", -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans", "Meiryo", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 24px 64px;
}
header.top {
  color: #ffffff;
  margin-bottom: 28px;
}
header.top .title-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
header.top h1 {
  margin: 0;
  font-size: 22px;
  letter-spacing: 0.04em;
  font-weight: 700;
}
header.top .updated {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.75);
}
.summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 18px;
}
.card {
  background: var(--card);
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 20px 22px;
}
.kpi .label {
  font-size: 12px;
  color: var(--muted);
  font-weight: 500;
  letter-spacing: 0.06em;
}
.kpi .value {
  margin-top: 6px;
  font-size: 26px;
  font-weight: 700;
  color: var(--ink);
}
.kpi.pl .value {
  font-size: 32px;
}
.kpi.pl .sub {
  margin-top: 4px;
  font-size: 16px;
  font-weight: 700;
}
.pos { color: var(--green) !important; }
.neg { color: var(--red) !important; }
.section-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--accent-strong);
  margin: 0 0 14px;
  letter-spacing: 0.04em;
}
.toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.toolbar button {
  background: #ffffff;
  color: var(--accent-strong);
  border: 1px solid var(--border);
  padding: 8px 16px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.toolbar button:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.toolbar button.active {
  background: var(--accent-strong);
  color: #ffffff;
  border-color: var(--accent-strong);
}
.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 22px;
}
.chart-card {
  padding: 20px 22px 18px;
}
.chart-wrap {
  position: relative;
  height: 320px;
}
.table-card { padding: 20px 22px 8px; }
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
thead th {
  text-align: right;
  font-weight: 600;
  font-size: 12px;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  padding: 10px 12px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  letter-spacing: 0.04em;
}
thead th.left { text-align: left; }
thead th:hover { color: var(--accent); }
thead th .arrow {
  display: inline-block;
  width: 12px;
  margin-left: 4px;
  color: var(--accent);
}
tbody td {
  padding: 12px;
  border-bottom: 1px solid var(--border);
  text-align: right;
  white-space: nowrap;
}
tbody td.left { text-align: left; }
tbody tr:hover { background: var(--card-2); }
tbody tr:last-child td { border-bottom: none; }
.code {
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  font-size: 13px;
}
.num { font-variant-numeric: tabular-nums; }
.pl-cell { font-weight: 600; }
.pl-cell.pos { color: var(--green); }
.pl-cell.neg { color: var(--red); }
.empty {
  text-align: center;
  color: var(--muted);
  padding: 28px 0;
  font-size: 14px;
}
@media (max-width: 820px) {
  .summary { grid-template-columns: 1fr; }
  .charts { grid-template-columns: 1fr; }
  .chart-wrap { height: 280px; }
  header.top h1 { font-size: 18px; }
  .kpi.pl .value { font-size: 26px; }
  html, body {
    background: linear-gradient(180deg, #0f1b2d 0%, #142540 360px, #f4f7fb 360px, #f4f7fb 100%);
  }
}
</style>
</head>
<body>
<div class="container">
  <header class="top">
    <div class="title-row">
      <h1>ポートフォリオ ダッシュボード</h1>
      <div class="updated">最終更新日時: <span id="updated-at"></span></div>
    </div>
    <div class="summary">
      <div class="card kpi">
        <div class="label">総取得額</div>
        <div class="value" id="kpi-cost"></div>
      </div>
      <div class="card kpi">
        <div class="label">現在評価額</div>
        <div class="value" id="kpi-value"></div>
      </div>
      <div class="card kpi pl">
        <div class="label">含み損益</div>
        <div class="value" id="kpi-pl"></div>
        <div class="sub" id="kpi-pl-pct"></div>
      </div>
    </div>
  </header>

  <div class="charts">
    <div class="card chart-card">
      <h2 class="section-title">取得額構成比</h2>
      <div class="chart-wrap"><canvas id="chart-donut"></canvas></div>
    </div>
    <div class="card chart-card">
      <h2 class="section-title">銘柄別 含み損益</h2>
      <div class="chart-wrap"><canvas id="chart-bar"></canvas></div>
    </div>
  </div>

  <div class="card table-card">
    <h2 class="section-title">保有銘柄一覧</h2>
    <div class="toolbar" id="filter-bar">
      <button data-filter="all" class="active">全件</button>
      <button data-filter="profit">含み益のみ</button>
      <button data-filter="loss">含み損のみ</button>
    </div>
    <div class="table-wrap">
      <table id="holdings-table">
        <thead>
          <tr>
            <th class="left" data-key="code">銘柄コード<span class="arrow"></span></th>
            <th class="left" data-key="name">銘柄名<span class="arrow"></span></th>
            <th data-key="shares">株数<span class="arrow"></span></th>
            <th data-key="cost_price">取得単価<span class="arrow"></span></th>
            <th data-key="current_price">現在株価<span class="arrow"></span></th>
            <th data-key="market_value">評価額<span class="arrow"></span></th>
            <th data-key="pl">含み損益(¥)<span class="arrow"></span></th>
            <th data-key="pl_pct">含み損益(%)<span class="arrow"></span></th>
          </tr>
        </thead>
        <tbody id="holdings-body"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
const DATA = __DATA__;

const yen = (n) => "¥" + Math.round(n).toLocaleString("ja-JP");
const yenSigned = (n) => (n >= 0 ? "+" : "-") + "¥" + Math.round(Math.abs(n)).toLocaleString("ja-JP");
const pct = (n) => (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
const fmtPrice = (n) => "¥" + n.toLocaleString("ja-JP", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

const COLORS = ["#1f3a68", "#3b6db5", "#5a9bd8", "#8ec5f0", "#b9d8f2", "#2d4f7c", "#4a7bb8"];

let state = {
  filter: "all",
  sortKey: "market_value",
  sortDir: "desc",
};

document.getElementById("updated-at").textContent = DATA.updated_at;

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
}

function getFiltered() {
  if (state.filter === "profit") return DATA.holdings.filter((h) => h.pl >= 0);
  if (state.filter === "loss") return DATA.holdings.filter((h) => h.pl < 0);
  return DATA.holdings.slice();
}

function getSorted(rows) {
  const k = state.sortKey;
  const dir = state.sortDir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const av = a[k];
    const bv = b[k];
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return String(av).localeCompare(String(bv), "ja") * dir;
  });
}

function renderTable() {
  const tbody = document.getElementById("holdings-body");
  const rows = getSorted(getFiltered());
  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">該当する銘柄がありません</td></tr>';
  } else {
    tbody.innerHTML = rows.map((r) => {
      const cls = r.pl >= 0 ? "pos" : "neg";
      return `
      <tr>
        <td class="left code">${r.code}</td>
        <td class="left">${r.name}</td>
        <td class="num">${r.shares.toLocaleString("ja-JP")}</td>
        <td class="num">${fmtPrice(r.cost_price)}</td>
        <td class="num">${fmtPrice(r.current_price)}</td>
        <td class="num">${yen(r.market_value)}</td>
        <td class="num pl-cell ${cls}">${yenSigned(r.pl)}</td>
        <td class="num pl-cell ${cls}">${pct(r.pl_pct)}</td>
      </tr>`;
    }).join("");
  }
  document.querySelectorAll("thead th").forEach((th) => {
    const arrow = th.querySelector(".arrow");
    if (!arrow) return;
    if (th.dataset.key === state.sortKey) {
      arrow.textContent = state.sortDir === "asc" ? "▲" : "▼";
    } else {
      arrow.textContent = "";
    }
  });
}

document.querySelectorAll("thead th").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDir = (key === "code" || key === "name") ? "asc" : "desc";
    }
    renderTable();
  });
});

document.querySelectorAll("#filter-bar button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#filter-bar button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.filter;
    renderTable();
  });
});

function renderDonut() {
  const ctx = document.getElementById("chart-donut").getContext("2d");
  const labels = DATA.holdings.map((h) => h.name);
  const data = DATA.holdings.map((h) => h.cost_total);
  new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: labels.map((_, i) => COLORS[i % COLORS.length]),
        borderColor: "#ffffff",
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: {
          position: "right",
          labels: {
            font: { family: "'Noto Sans JP', sans-serif", size: 12 },
            color: "#1a2842",
            boxWidth: 12,
            padding: 10,
          },
        },
        tooltip: {
          callbacks: {
            label: (item) => {
              const total = data.reduce((a, b) => a + b, 0);
              const v = item.parsed;
              const p = total ? (v / total * 100).toFixed(1) : "0.0";
              return `${item.label}: ¥${Math.round(v).toLocaleString("ja-JP")} (${p}%)`;
            },
          },
        },
      },
    },
  });
}

function renderBar() {
  const ctx = document.getElementById("chart-bar").getContext("2d");
  const sorted = DATA.holdings.slice().sort((a, b) => a.pl - b.pl);
  const labels = sorted.map((h) => h.name);
  const data = sorted.map((h) => h.pl);
  const colors = data.map((v) => v >= 0 ? "#1e9e6a" : "#d64545");
  new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: colors,
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item) => {
              const v = item.parsed.x;
              return (v >= 0 ? "+" : "-") + "¥" + Math.round(Math.abs(v)).toLocaleString("ja-JP");
            },
          },
        },
      },
      scales: {
        x: {
          grid: { color: "#eef1f6" },
          ticks: {
            font: { family: "'Noto Sans JP', sans-serif" },
            color: "#6b7a90",
            callback: (v) => "¥" + Number(v).toLocaleString("ja-JP"),
          },
        },
        y: {
          grid: { display: false },
          ticks: {
            font: { family: "'Noto Sans JP', sans-serif", size: 12 },
            color: "#1a2842",
          },
        },
      },
    },
  });
}

renderSummary();
renderTable();
renderDonut();
renderBar();
</script>
</body>
</html>
"""


def main() -> None:
    snapshot = build_snapshot()
    payload = json.dumps(snapshot, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__DATA__", payload)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"\nwrote {OUTPUT_PATH} ({len(html):,} bytes)")
    s = snapshot["summary"]
    print(f"  cost  : ¥{s['total_cost']:,.0f}")
    print(f"  value : ¥{s['total_value']:,.0f}")
    print(f"  pl    : ¥{s['total_pl']:,.0f} ({s['total_pl_pct']:.2f}%)")


if __name__ == "__main__":
    main()
