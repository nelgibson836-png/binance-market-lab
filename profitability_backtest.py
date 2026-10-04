import json
import math
import random
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://data-api.binance.vision"
SYMBOL = "BTCUSDT"
DAYS = 180
INTERVAL = "1m"
LIMIT = 1000
OUTPUT_FILE = "data/profitability_backtest.json"
REPORT_VERSION = "1.0"

TRAIN_DAYS = 21
TEST_DAYS = 7
STEP_DAYS = 7
MIN_TRAIN_SAMPLES = 100

ENTRY_MINUTES = (1, 3, 5)
HORIZONS = (1, 3, 5, 15)
PROBABILITY_THRESHOLDS = (0.55, 0.60, 0.65, 0.70)

# All-in round-trip cost stress cases. These are deliberately generic because
# the actual fee/slippage depends on spot vs futures, VIP tier, and execution.
ROUND_TRIP_COSTS = (0.0, 0.001, 0.002, 0.003)
BOOTSTRAP_SAMPLES = 1000
MONTE_CARLO_SIMS = 1000
MONTE_CARLO_TRADES = 100

MOVE_BINS = [
    -math.inf, -0.30, -0.20, -0.10, -0.05,
    0.00, 0.05, 0.10, 0.20, 0.30, math.inf
]


def get_json(path):
    request = Request(
        BASE_URL + path,
        headers={"User-Agent": "binance-market-lab/profitability-backtest"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_klines():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - DAYS * 24 * 60 * 60 * 1000
    rows = []
    current_start = start_ms

    while current_start < now_ms:
        path = (
            f"/api/v3/klines?symbol={SYMBOL}&interval={INTERVAL}"
            f"&startTime={current_start}&endTime={now_ms}&limit={LIMIT}"
        )
        try:
            data = get_json(path)
        except (HTTPError, URLError, TimeoutError):
            raise

        if not data:
            break

        for k in data:
            close_time = int(k[6])
            if close_time >= now_ms:
                continue
            rows.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "close": float(k[4]),
                "close_time": close_time,
                "is_closed": True,
            })

        next_start = int(data[-1][0]) + 60_000
        if next_start <= current_start:
            break
        current_start = next_start
        time.sleep(0.08)

    unique = {row["open_time"]: row for row in rows}
    return sorted(unique.values(), key=lambda x: x["open_time"])


def pct_change(a, b):
    return 0.0 if a == 0 else ((b - a) / a) * 100.0


def move_bucket(move):
    for index in range(len(MOVE_BINS) - 1):
        if MOVE_BINS[index] <= move < MOVE_BINS[index + 1]:
            return index
    return len(MOVE_BINS) - 2


def build_candles(rows):
    grouped = {}
    for row in rows:
        start = (row["open_time"] // 900_000) * 900_000
        grouped.setdefault(start, []).append(row)

    candles = []
    for start, candle_rows in sorted(grouped.items()):
        candle_rows.sort(key=lambda x: x["open_time"])
        if len(candle_rows) != 15:
            continue

        expected = [start + i * 60_000 for i in range(15)]
        if [r["open_time"] for r in candle_rows] != expected:
            continue

        actual = 1 if candle_rows[14]["close"] > candle_rows[0]["open"] else 0
        if candle_rows[14]["close"] == candle_rows[0]["open"]:
            continue

        candles.append({
            "candle_start": start,
            "rows": candle_rows,
            "actual": actual,
        })
    return candles


def build_observations(candles, rows):
    close_map = {row["open_time"]: row["close"] for row in rows}
    observations = []
    for candle in candles:
        rows_15m = candle["rows"]
        base = rows_15m[0]["open"]
        for minute in ENTRY_MINUTES:
            current_row = rows_15m[minute - 1]
            current = current_row["close"]
            future_closes = {}
            for horizon in HORIZONS:
                target_time = current_row["open_time"] + horizon * 60_000
                future = close_map.get(target_time)
                if future is not None:
                    future_closes[horizon] = future
            move = pct_change(base, current)
            observations.append({
                "candle_start": candle["candle_start"],
                "minute": minute,
                "move": move,
                "move_bucket": move_bucket(move),
                "actual": candle["actual"],
                "current_close": current,
                "future_closes": future_closes,
            })
    return observations


def fit_model(train):
    stats = {}
    for obs in train:
        key = (obs["minute"], obs["move_bucket"])
        item = stats.setdefault(key, [0, 0])
        item[0] += 1
        item[1] += obs["actual"]

    probabilities = {}
    for key, (count, wins) in stats.items():
        if count >= MIN_TRAIN_SAMPLES:
            probabilities[key] = wins / count
    return probabilities


def build_fold_ranges(candles):
    if not candles:
        return []

    first_start = candles[0]["candle_start"]
    last_start = candles[-1]["candle_start"]
    day_ms = 24 * 60 * 60 * 1000
    train_ms = TRAIN_DAYS * day_ms
    test_ms = TEST_DAYS * day_ms
    step_ms = STEP_DAYS * day_ms

    folds = []
    train_start = first_start
    while train_start + train_ms + test_ms <= last_start + 900_000:
        train_end = train_start + train_ms
        test_end = train_end + test_ms
        folds.append((train_start, train_end, test_end))
        train_start += step_ms
    return folds


def in_range(rows, start_ms, end_ms):
    return [row for row in rows if start_ms <= row["candle_start"] < end_ms]


def trade_return(obs, horizon, direction, round_trip_cost):
    future_close = obs["future_closes"].get(horizon)
    if future_close is None or obs["current_close"] == 0:
        return None

    gross = (future_close - obs["current_close"]) / obs["current_close"]
    strategy_gross = gross if direction == 1 else -gross
    return strategy_gross - round_trip_cost


def max_drawdown(returns):
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return max_dd


def bootstrap_mean_ci(candle_returns):
    if len(candle_returns) < 2:
        return None

    rng = random.Random(42)
    n = len(candle_returns)
    means = []
    for _ in range(BOOTSTRAP_SAMPLES):
        total = 0.0
        for _ in range(n):
            total += candle_returns[rng.randrange(n)]
        means.append(total / n)

    means.sort()
    low = means[int(0.025 * (len(means) - 1))]
    high = means[int(0.975 * (len(means) - 1))]
    return {
        "low": round(low, 8),
        "high": round(high, 8),
    }


def calibration_summary(predictions):
    if not predictions:
        return None

    brier = sum(
        (p["probability"] - p["actual"]) ** 2
        for p in predictions
    ) / len(predictions)

    bins = []
    ranges = ((0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01))
    for lo, hi in ranges:
        subset = [p for p in predictions if lo <= p["probability"] < hi]
        if not subset:
            continue
        bins.append({
            "range": f"{lo:.2f}-{min(1.0, hi):.2f}",
            "count": len(subset),
            "mean_probability": round(sum(p["probability"] for p in subset) / len(subset), 6),
            "observed_rate": round(sum(p["actual"] for p in subset) / len(subset), 6),
        })

    return {
        "brier_score": round(brier, 8),
        "mean_probability": round(sum(p["probability"] for p in predictions) / len(predictions), 6),
        "observed_rate": round(sum(p["actual"] for p in predictions) / len(predictions), 6),
        "bins": bins,
    }


def evaluate_configuration(observations, threshold, entry_minute, horizon, cost):
    fold_results = []
    all_returns = []
    all_candle_returns = []
    baseline_returns = []
    predictions = []

    candle_starts = sorted({obs["candle_start"] for obs in observations})
    if not candle_starts:
        return None

    day_ms = 24 * 60 * 60 * 1000
    first_start = candle_starts[0]
    last_start = candle_starts[-1]
    train_ms = TRAIN_DAYS * day_ms
    test_ms = TEST_DAYS * day_ms
    step_ms = STEP_DAYS * day_ms

    fold_start = first_start
    while fold_start + train_ms + test_ms <= last_start + 900_000:
        train_end = fold_start + train_ms
        test_end = train_end + test_ms

        train = [
            x for x in observations
            if fold_start <= x["candle_start"] < train_end
        ]
        test = [
            x for x in observations
            if train_end <= x["candle_start"] < test_end
            and x["minute"] == entry_minute
        ]

        probabilities = fit_model(train)
        fold_returns = []
        fold_baseline = []
        candle_map = {}

        for obs in test:
            probability_up = probabilities.get(
                (obs["minute"], obs["move_bucket"])
            )
            if probability_up is None:
                continue

            model_probability = (
                probability_up
                if probability_up >= 0.5
                else 1.0 - probability_up
            )
            model_pred_correct = int(
                (probability_up >= 0.5 and obs["actual"] == 1)
                or (probability_up < 0.5 and obs["actual"] == 0)
            )
            predictions.append({
                "probability": model_probability,
                "actual": model_pred_correct,
            })

            if probability_up >= threshold:
                direction = 1
            elif probability_up <= 1.0 - threshold:
                direction = 0
            else:
                continue

            result = trade_return(obs, horizon, direction, cost)
            baseline_direction = 1 if obs["move"] > 0 else 0
            baseline_result = trade_return(
                obs, horizon, baseline_direction, cost
            )
            if result is None or baseline_result is None:
                continue

            fold_returns.append(result)
            fold_baseline.append(baseline_result)
            all_returns.append(result)
            baseline_returns.append(baseline_result)
            candle_map[obs["candle_start"]] = result

        all_candle_returns.extend(candle_map.values())

        fold_results.append({
            "fold_start": datetime.fromtimestamp(
                fold_start / 1000, timezone.utc
            ).isoformat(),
            "test_end": datetime.fromtimestamp(
                test_end / 1000, timezone.utc
            ).isoformat(),
            "signals": len(fold_returns),
            "mean_net_return": round(
                sum(fold_returns) / len(fold_returns), 8
            ) if fold_returns else None,
            "win_rate": round(
                sum(1 for x in fold_returns if x > 0)
                / len(fold_returns) * 100, 2
            ) if fold_returns else None,
            "baseline_mean_net_return": round(
                sum(fold_baseline) / len(fold_baseline), 8
            ) if fold_baseline else None,
        })

        fold_start += step_ms

    if not all_returns:
        return None

    equity = 1.0
    for value in all_returns:
        equity *= 1.0 + value

    fold_means = [
        f["mean_net_return"]
        for f in fold_results
        if f["mean_net_return"] is not None
    ]
    profitable_folds = sum(1 for value in fold_means if value > 0)
    baseline_mean = sum(baseline_returns) / len(baseline_returns)
    model_mean = sum(all_returns) / len(all_returns)

    return {
        "threshold": threshold,
        "entry_minute": entry_minute,
        "horizon_minutes": horizon,
        "round_trip_cost": cost,
        "trades": len(all_returns),
        "wins": sum(1 for x in all_returns if x > 0),
        "win_rate": round(
            sum(1 for x in all_returns if x > 0)
            / len(all_returns) * 100, 2
        ),
        "mean_net_return": round(model_mean, 8),
        "median_net_return": round(
            sorted(all_returns)[len(all_returns) // 2], 8
        ),
        "sum_net_return": round(sum(all_returns), 8),
        "compounded_return": round(equity - 1.0, 6),
        "max_drawdown": round(max_drawdown(all_returns), 6),
        "profitable_folds": profitable_folds,
        "total_folds": len(fold_means),
        "fold_profitability_rate": round(
            profitable_folds / len(fold_means), 4
        ) if fold_means else None,
        "baseline_mean_net_return": round(baseline_mean, 8),
        "model_minus_baseline": round(model_mean - baseline_mean, 8),
        "block_bootstrap_mean_ci95": bootstrap_mean_ci(
            all_candle_returns
        ),
        "calibration": calibration_summary(predictions),
        "folds": fold_results,
        "_returns": all_returns,
    }


def monte_carlo(returns):
    if not returns:
        return None
    rng = random.Random(20261004)
    terminals = []

    n = min(MONTE_CARLO_TRADES, len(returns))
    for _ in range(MONTE_CARLO_SIMS):
        equity = 1.0
        for _ in range(n):
            equity *= 1.0 + returns[rng.randrange(len(returns))]
        terminals.append(equity - 1.0)

    terminals.sort()
    return {
        "trades_per_path": n,
        "simulations": MONTE_CARLO_SIMS,
        "terminal_return_p05": round(terminals[int(0.05 * (len(terminals) - 1))], 6),
        "terminal_return_p50": round(terminals[int(0.50 * (len(terminals) - 1))], 6),
        "terminal_return_p95": round(terminals[int(0.95 * (len(terminals) - 1))], 6),
        "probability_positive": round(sum(1 for x in terminals if x > 0) / len(terminals), 4),
    }


def main():
    rows = fetch_klines()
    candles = build_candles(rows)
    observations = build_observations(candles, rows)

    configurations = []

    for threshold in PROBABILITY_THRESHOLDS:
        for entry_minute in ENTRY_MINUTES:
            for horizon in HORIZONS:
                for cost in ROUND_TRIP_COSTS:
                    result = evaluate_configuration(
                        observations,
                        threshold,
                        entry_minute,
                        horizon,
                        cost,
                    )
                    if result:
                        configurations.append(result)

    stress_cost = 0.002
    stress_candidates = [
        x for x in configurations
        if abs(x["round_trip_cost"] - stress_cost) < 1e-12
    ]
    stress_candidates.sort(
        key=lambda x: x["mean_net_return"],
        reverse=True,
    )

    for item in stress_candidates[:5]:
        item["monte_carlo"] = monte_carlo(item["_returns"])

    for item in configurations:
        item.pop("_returns", None)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "days": DAYS,
        "walk_forward": {
            "train_days": TRAIN_DAYS,
            "test_days": TEST_DAYS,
            "step_days": STEP_DAYS,
            "folds": len({
                (f["fold_start"], f["test_end"])
                for result in configurations
                for f in result["folds"]
            }) if configurations else 0,
        },
        "cost_definition": (
            "generic all-in round-trip stress; tune to actual "
            "Binance venue and fee tier before live use"
        ),
        "configurations": configurations,
        "notes": [
            "Only complete closed 1m data is used.",
            "Direction is selected strictly from training data in each walk-forward fold.",
            "A fixed entry minute and fixed horizon create one trade opportunity per 15m candle.",
            "Horizon prices come from the continuous 1m series, including across 15m boundaries.",
            "Round-trip costs are stress cases, not reconstructed historical fills.",
            "This is an economic viability test, not proof of executable profitability.",
            "Model-vs-baseline alpha is reported on identical selected signals.",
            "Block bootstrap uses one return per 15m candle to reduce dependence from overlapping observations.",
            "Monte Carlo is reported only for the strongest configurations at 20bp round-trip stress.",
        ],
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)

    print(json.dumps({
        "symbol": SYMBOL,
        "candles_15m": len(candles),
        "observations": len(observations),
        "configurations": len(configurations),
        "output": OUTPUT_FILE,
    }, indent=2))


if __name__ == "__main__":
    main()
