import json
import math
import time
from datetime import datetime, timezone

from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://data-api.binance.vision"
SYMBOL = "BTCUSDT"
DAYS = 60
INTERVAL = "1m"
LIMIT = 1000
OUTPUT_FILE = "data/challenger_execution_v1.json"

TRAIN_DAYS = 21
TEST_DAYS = 7
STEP_DAYS = 7
ENTRY_MINUTES = tuple(range(5, 14))
MOVE_THRESHOLDS_BPS = (0, 2, 5, 10, 20, 30)
MIN_TRAIN_TRADES = 100

# Conservative research assumptions, not account-specific Binance fees.
# fee/slippage apply per side; spread is modeled as a round-trip cost.
FEE_BPS_PER_SIDE = 10.0
SLIPPAGE_BPS_PER_SIDE = 2.0
SPREAD_BPS_ROUND_TRIP = 2.0


def get_json(path):
    request = Request(
        BASE_URL + path,
        headers={"User-Agent": "binance-market-lab/challenger-execution-v1"},
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_klines():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - DAYS * 24 * 60 * 60 * 1000
    rows = []
    current_start = start_ms

    while current_start < now_ms:
        path = (
            f"/api/v3/klines?symbol={SYMBOL}"
            f"&interval={INTERVAL}&startTime={current_start}"
            f"&endTime={now_ms}&limit={LIMIT}"
        )
        data = get_json(path)
        if not data:
            break

        for k in data:
            close_time = int(k[6])
            if close_time >= now_ms:
                continue
            rows.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "close_time": close_time,
                "is_closed": True,
            })

        last_open_time = int(data[-1][0])
        next_start = last_open_time + 60_000
        if next_start <= current_start:
            break
        current_start = next_start
        time.sleep(0.08)

    unique = {row["open_time"]: row for row in rows}
    return sorted(unique.values(), key=lambda row: row["open_time"])


def pct_change(a, b):
    return 0.0 if a == 0 else (b - a) / a * 100.0


def build_candles(rows):
    grouped = {}
    for row in rows:
        start = (row["open_time"] // 900_000) * 900_000
        grouped.setdefault(start, []).append(row)

    candles = []
    for start, items in sorted(grouped.items()):
        items.sort(key=lambda row: row["open_time"])
        expected = [start + i * 60_000 for i in range(15)]
        if len(items) != 15:
            continue
        if [row["open_time"] for row in items] != expected:
            continue
        if not all(row["is_closed"] for row in items):
            continue

        candles.append({
            "start": start,
            "rows": items,
            "open": items[0]["open"],
            "close": items[-1]["close"],
        })
    return candles


def net_return(entry, exit_price, side):
    gross = side * ((exit_price - entry) / entry)
    total_cost = (
        2 * (FEE_BPS_PER_SIDE + SLIPPAGE_BPS_PER_SIDE)
        + SPREAD_BPS_ROUND_TRIP
    ) / 10_000.0
    return gross - total_cost


def trade_for(candle, minute, threshold_bps):
    rows = candle["rows"]

    # Decision uses the close of minute N.
    decision_close = rows[minute - 1]["close"]
    move_bps = (decision_close / candle["open"] - 1.0) * 10_000.0

    if abs(move_bps) < threshold_bps or move_bps == 0:
        return None

    side = 1 if move_bps > 0 else -1

    # Entry is the next 1m open, eliminating same-candle close execution
    # and preventing use of future information.
    entry = rows[minute]["open"]
    exit_price = rows[14]["close"]
    return {
        "candle_start": candle["start"],
        "decision_minute": minute,
        "threshold_bps": threshold_bps,
        "side": side,
        "move_bps": move_bps,
        "entry": entry,
        "exit": exit_price,
        "gross_return": side * ((exit_price - entry) / entry),
        "net_return": net_return(entry, exit_price, side),
    }


def candidate_stats(candles, minute, threshold_bps):
    trades = []
    for candle in candles:
        trade = trade_for(candle, minute, threshold_bps)
        if trade is not None:
            trades.append(trade)

    if not trades:
        return None

    net = [trade["net_return"] for trade in trades]
    gross = [trade["gross_return"] for trade in trades]
    wins = sum(value > 0 for value in net)
    positive = sum(value for value in net if value > 0)
    negative = -sum(value for value in net if value < 0)

    return {
        "minute": minute,
        "threshold_bps": threshold_bps,
        "trades": len(trades),
        "win_rate": wins / len(trades),
        "mean_net_return": sum(net) / len(net),
        "mean_gross_return": sum(gross) / len(gross),
        "profit_factor": (
            positive / negative if negative > 0 else math.inf
        ),
    }


def choose_candidate(train_candles):
    candidates = []
    for minute in ENTRY_MINUTES:
        for threshold in MOVE_THRESHOLDS_BPS:
            stats = candidate_stats(train_candles, minute, threshold)
            if stats and stats["trades"] >= MIN_TRAIN_TRADES:
                candidates.append(stats)

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: (
            item["mean_net_return"],
            item["win_rate"],
            -item["threshold_bps"],
        ),
    )


def evaluate_candidate(candles, minute, threshold_bps):
    trades = []
    for candle in candles:
        trade = trade_for(candle, minute, threshold_bps)
        if trade is not None:
            trades.append(trade)

    if not trades:
        return {
            "trades": 0,
            "win_rate": None,
            "mean_net_return": None,
            "cumulative_net_return": None,
            "max_drawdown": None,
            "profit_factor": None,
            "daily_net_return": [],
        }

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    positive = 0.0
    negative = 0.0
    wins = 0
    daily = {}

    for trade in trades:
        r = trade["net_return"]
        equity *= 1.0 + r
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0
        max_drawdown = min(max_drawdown, drawdown)

        if r > 0:
            wins += 1
            positive += r
        elif r < 0:
            negative -= r

        day = datetime.fromtimestamp(
            trade["candle_start"] / 1000, timezone.utc
        ).date().isoformat()
        daily[day] = daily.get(day, 0.0) + r

    net_values = [trade["net_return"] for trade in trades]
    gross_values = [trade["gross_return"] for trade in trades]

    return {
        "trades": len(trades),
        "win_rate": wins / len(trades),
        "mean_net_return": sum(net_values) / len(net_values),
        "mean_gross_return": sum(gross_values) / len(gross_values),
        "cumulative_net_return": equity - 1.0,
        "max_drawdown": max_drawdown,
        "profit_factor": positive / negative if negative > 0 else None,
        "daily_net_return": [
            {"date": day, "net_return": value}
            for day, value in sorted(daily.items())
        ],
    }


def baseline_always_long(candles, minute):
    trades = []
    for candle in candles:
        rows = candle["rows"]
        entry = rows[minute]["open"]
        exit_price = rows[14]["close"]
        gross = (exit_price - entry) / entry
        total_cost = (
            2 * (FEE_BPS_PER_SIDE + SLIPPAGE_BPS_PER_SIDE)
            + SPREAD_BPS_ROUND_TRIP
        ) / 10_000.0
        trades.append(gross - total_cost)

    if not trades:
        return None
    return {
        "trades": len(trades),
        "mean_net_return": sum(trades) / len(trades),
        "cumulative_net_return": math.prod(1 + value for value in trades) - 1.0,
    }


def build_fold_ranges(candles):
    if not candles:
        return []

    first = candles[0]["start"]
    last = candles[-1]["start"]
    day_ms = 24 * 60 * 60 * 1000
    train_ms = TRAIN_DAYS * day_ms
    test_ms = TEST_DAYS * day_ms
    step_ms = STEP_DAYS * day_ms

    folds = []
    start = first
    while start + train_ms + test_ms <= last + 900_000:
        train_end = start + train_ms
        test_end = train_end + test_ms
        folds.append((start, train_end, test_end))
        start += step_ms
    return folds


def in_range(candles, start, end):
    return [
        candle for candle in candles
        if start <= candle["start"] < end
    ]


def main():
    rows = fetch_klines()
    candles = build_candles(rows)
    folds = build_fold_ranges(candles)

    fold_results = []
    all_oos_trades = []

    for index, (train_start, train_end, test_end) in enumerate(folds, start=1):
        train = in_range(candles, train_start, train_end)
        test = in_range(candles, train_end, test_end)

        selected = choose_candidate(train)
        if selected is None:
            fold_results.append({
                "fold": index,
                "train_candles": len(train),
                "test_candles": len(test),
                "selected": None,
                "test": None,
            })
            continue

        test_metrics = evaluate_candidate(
            test,
            selected["minute"],
            selected["threshold_bps"],
        )

        fold_results.append({
            "fold": index,
            "train_start": datetime.fromtimestamp(
                train_start / 1000, timezone.utc
            ).isoformat(),
            "train_end": datetime.fromtimestamp(
                train_end / 1000, timezone.utc
            ).isoformat(),
            "test_end": datetime.fromtimestamp(
                test_end / 1000, timezone.utc
            ).isoformat(),
            "train_candles": len(train),
            "test_candles": len(test),
            "selected": selected,
            "test": {
                key: value
                for key, value in test_metrics.items()
                if key != "daily_net_return"
            },
            "baseline_always_long": baseline_always_long(
                test, selected["minute"]
            ),
        })

        for candle in test:
            trade = trade_for(
                candle, selected["minute"], selected["threshold_bps"]
            )
            if trade is not None:
                all_oos_trades.append(trade)

    if all_oos_trades:
        oos_returns = [trade["net_return"] for trade in all_oos_trades]
        oos_wins = sum(value > 0 for value in oos_returns)
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in oos_returns:
            equity *= 1.0 + value
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity / peak - 1.0)

        oos_summary = {
            "trades": len(all_oos_trades),
            "win_rate": oos_wins / len(all_oos_trades),
            "mean_net_return": sum(oos_returns) / len(oos_returns),
            "cumulative_net_return": equity - 1.0,
            "max_drawdown": max_drawdown,
            "positive_folds": sum(
                1
                for fold in fold_results
                if fold.get("test")
                and fold["test"].get("cumulative_net_return", 0) > 0
            ),
            "tested_folds": sum(
                1 for fold in fold_results if fold.get("test")
            ),
        }
    else:
        oos_summary = {
            "trades": 0,
            "win_rate": None,
            "mean_net_return": None,
            "cumulative_net_return": None,
            "max_drawdown": None,
            "positive_folds": 0,
            "tested_folds": 0,
        }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "days": DAYS,
        "method": "walk_forward_challenger_execution",
        "train_days": TRAIN_DAYS,
        "test_days": TEST_DAYS,
        "step_days": STEP_DAYS,
        "entry_minutes": list(ENTRY_MINUTES),
        "move_thresholds_bps": list(MOVE_THRESHOLDS_BPS),
        "min_train_trades": MIN_TRAIN_TRADES,
        "cost_model": {
            "fee_bps_per_side": FEE_BPS_PER_SIDE,
            "slippage_bps_per_side": SLIPPAGE_BPS_PER_SIDE,
            "spread_bps_round_trip": SPREAD_BPS_ROUND_TRIP,
            "total_round_trip_bps": 2 * (
                FEE_BPS_PER_SIDE + SLIPPAGE_BPS_PER_SIDE
            ) + SPREAD_BPS_ROUND_TRIP,
        },
        "samples_1m": len(rows),
        "candles_15m": len(candles),
        "folds": len(folds),
        "oos_summary": oos_summary,
        "fold_results": fold_results,
        "notes": [
            "Only complete closed 15m candles are used.",
            "The decision is made from minute N close; entry occurs at minute N+1 open.",
            "The final 15m close is used only for settlement after the decision.",
            "Candidate minute and movement threshold are selected on training data only.",
            "Costs are research assumptions, not the user's account fee tier.",
            "DOWN signals represent a directional short assumption and are not live trading.",
            "No existing historical dataset, reference backtest, or main-branch report is modified.",
        ],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)

    print(json.dumps({
        "oos_summary": oos_summary,
        "folds": len(fold_results),
        "output": OUTPUT_FILE,
    }, indent=2))


if __name__ == "__main__":
    main()
