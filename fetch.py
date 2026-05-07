"""保有株のブリーフィング・ダッシュボード用データ取得スクリプト

これは投資推奨を生成するものではなく、portfolio.csv を起点に
価格・前日比・52週レンジ・含み損益・分割調整を機械的に集計し、
data.json として書き出すだけの「情報整理ツール」です。

実行例:
    python3 fetch.py
    python3 fetch.py --csv portfolio.csv --out data.json

依存:
    pip install yfinance pandas
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    import yfinance as yf
except ImportError:
    print("yfinance が見つかりません。`pip install yfinance pandas` を実行してください。", file=sys.stderr)
    raise

JST = timezone(timedelta(hours=9))


@dataclass
class Holding:
    code: str
    name: str
    shares_original: float
    buy_price_original: float
    acquired_at: str | None  # YYYY-MM-DD or None

    # 分割調整後（fetch 後に埋まる）
    shares: float = 0.0
    buy_price: float = 0.0
    split_adjusted: bool = False
    split_note: str = ""

    # 価格情報
    current_price: float | None = None
    day_change_pct: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None

    # 損益
    cost: float = 0.0
    value: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0

    # ニュース・分析（このスクリプトでは空。別工程で埋める想定）
    news: list[dict[str, Any]] = field(default_factory=list)
    category: str = ""
    category_reason: str = ""
    watch_points: list[str] = field(default_factory=list)

    @property
    def ticker(self) -> str:
        # 4桁コードは東証として `.T` を付ける
        if self.code.isdigit() and len(self.code) == 4:
            return f"{self.code}.T"
        return self.code


def load_portfolio(csv_path: Path) -> list[Holding]:
    holdings: list[Holding] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("銘柄コード") or row.get("code") or "").strip()
            if not code:
                continue
            holdings.append(
                Holding(
                    code=code,
                    name=(row.get("銘柄名") or row.get("name") or "").strip(),
                    shares_original=float(row.get("保有株数") or row.get("shares") or 0),
                    buy_price_original=float(row.get("取得単価") or row.get("buy_price") or 0),
                    acquired_at=(row.get("取得日") or row.get("acquired_at") or "").strip() or None,
                )
            )
    return holdings


def apply_splits(h: Holding, splits_series) -> None:
    """取得日以降に発生した株式分割を、保有株数と取得単価に反映する。"""
    h.shares = h.shares_original
    h.buy_price = h.buy_price_original
    h.split_adjusted = False

    if h.acquired_at is None:
        h.split_note = "取得日が未記載のため、株式分割の自動調整は行えません。"
        return

    if splits_series is None or splits_series.empty:
        h.split_note = "yfinance から分割履歴が取得できなかったか、分割なし。"
        return

    try:
        acq = datetime.strptime(h.acquired_at, "%Y-%m-%d")
    except ValueError:
        h.split_note = f"取得日({h.acquired_at})の解析に失敗。分割調整は行いません。"
        return

    # tz aware の index に対しても比較できるように
    try:
        relevant = splits_series[splits_series.index.tz_localize(None) > acq]
    except (TypeError, AttributeError):
        relevant = splits_series[splits_series.index > acq]

    if relevant.empty:
        h.split_note = f"取得日({h.acquired_at})以降の分割なし。調整不要。"
        return

    notes = []
    for ts, ratio in relevant.items():
        if ratio and not math.isclose(ratio, 1.0):
            h.shares *= ratio
            h.buy_price /= ratio
            notes.append(f"{ts.strftime('%Y-%m-%d')} に 1:{ratio:g} 分割を適用")
            h.split_adjusted = True

    if notes:
        h.split_note = (
            "; ".join(notes)
            + f" → 株数 {h.shares_original:g}→{h.shares:g} / 取得単価 {h.buy_price_original:g}→{h.buy_price:g}円"
        )


def fetch_quote(h: Holding) -> None:
    t = yf.Ticker(h.ticker)

    # 価格情報（直近2営業日から day change を計算）
    hist = t.history(period="5d", auto_adjust=False)
    if not hist.empty:
        last = hist.iloc[-1]
        h.current_price = float(last["Close"])
        if len(hist) >= 2:
            prev = float(hist.iloc[-2]["Close"])
            if prev:
                h.day_change_pct = round((h.current_price - prev) / prev * 100, 2)

    # 52週レンジ
    hist_52w = t.history(period="1y", auto_adjust=False)
    if not hist_52w.empty:
        h.week52_high = round(float(hist_52w["High"].max()), 2)
        h.week52_low = round(float(hist_52w["Low"].min()), 2)

    # 株式分割
    splits = getattr(t, "splits", None)
    apply_splits(h, splits)


def compute_pnl(h: Holding) -> None:
    if h.current_price is None:
        return
    h.cost = round(h.shares * h.buy_price, 2)
    h.value = round(h.shares * h.current_price, 2)
    h.pnl = round(h.value - h.cost, 2)
    h.pnl_pct = round(h.pnl / h.cost * 100, 2) if h.cost else 0.0


def categorize(h: Holding) -> None:
    """説明可能な数値ルール: <=-20% 売却検討 / -20%<x<+5% 様子見 / >=+5% 継続保有"""
    if h.pnl_pct <= -20:
        h.category = "売却検討"
        h.category_reason = (
            f"含み損 {h.pnl_pct:.2f}% が -20% を下回る。"
            "本ダッシュボードの数値ルール上は売却検討区分。"
        )
    elif h.pnl_pct < 5:
        h.category = "様子見"
        h.category_reason = (
            f"含み損益率 {h.pnl_pct:.2f}% は -20%〜+5% のレンジ内。"
            "明確な売買シグナルが立っていないため様子見区分。"
        )
    else:
        h.category = "継続保有"
        h.category_reason = (
            f"含み益 {h.pnl_pct:.2f}% で +5% 以上。"
            "数値ルール上は継続保有区分。"
        )


def to_dict(h: Holding) -> dict[str, Any]:
    return {
        "code": h.code,
        "ticker": h.ticker,
        "name": h.name,
        "shares_original": h.shares_original,
        "buy_price_original": h.buy_price_original,
        "acquired_at": h.acquired_at,
        "split_adjusted": h.split_adjusted,
        "split_note": h.split_note,
        "shares": h.shares,
        "buy_price": round(h.buy_price, 2),
        "current_price": h.current_price,
        "day_change_pct": h.day_change_pct,
        "week52_high": h.week52_high,
        "week52_low": h.week52_low,
        "cost": h.cost,
        "value": h.value,
        "pnl": h.pnl,
        "pnl_pct": h.pnl_pct,
        "category": h.category,
        "category_reason": h.category_reason,
        "watch_points": h.watch_points,
        "news": h.news,
    }


def build_payload(holdings: list[Holding]) -> dict[str, Any]:
    total_cost = sum(h.cost for h in holdings)
    total_value = sum(h.value for h in holdings)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    counts: dict[str, int] = {"売却検討": 0, "様子見": 0, "継続保有": 0}
    for h in holdings:
        if h.category in counts:
            counts[h.category] += 1

    return {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "currency": "JPY",
        "data_source_note": "fetch.py により yfinance から取得。価格・ニュースには遅延・誤りがあり得ます。",
        "summary": {
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "category_counts": counts,
        },
        "category_rule": "PnL率 <= -20% → 売却検討 / -20% < PnL率 < +5% → 様子見 / PnL率 >= +5% → 継続保有",
        "stocks": [to_dict(h) for h in holdings],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="portfolio.csv → data.json")
    parser.add_argument("--csv", default="portfolio.csv")
    parser.add_argument("--out", default="data.json")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV が見つかりません: {csv_path}", file=sys.stderr)
        return 1

    holdings = load_portfolio(csv_path)
    if not holdings:
        print("portfolio.csv に銘柄行がありません。", file=sys.stderr)
        return 1

    for h in holdings:
        try:
            fetch_quote(h)
            compute_pnl(h)
            categorize(h)
            print(
                f"[OK] {h.code} {h.name} price={h.current_price} "
                f"pnl={h.pnl_pct:.2f}% category={h.category}"
            )
        except Exception as e:
            print(f"[NG] {h.code} {h.name}: {e}", file=sys.stderr)

    payload = build_payload(holdings)
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
