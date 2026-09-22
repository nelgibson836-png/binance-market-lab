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
OUTPUT_FILE = "data/backtest_15m.json"
MIN_TRAIN_SAMPLES = 100

TRAIN_DAYS = 21
TEST_DAYS = 7
STEP_DAYS = 7

MOVE_BINS = [
    -math.inf, -0.30, -0.20, -0.10, -0.05,
    0.00, 0.05, 0.10, 0.20, 0.30, math.inf
]
THRESHOLDS = (0.55, 0.60, 0.65, 0.70)
BOOTSTRAP_SAMPLES = 1000


def get_json(path):
    request = Request(
        BASE_URL + path,
        headers={"User-Agent": "binance-market-lab/backtest-15m"},
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
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": close_time,
                "trades": int(k[8]),
                "is_closed": True,
            })

        last_open_time = int(data[-1][0])
        next_start = last_open_time + 60_000
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


def build_candles_15m(rows):
    grouped = {}
    for row in rows:
        candle_start = (row["open_time"] // 900_000) * 900_000
        grouped.setdefault(candle_start, []).append(row)

    candles = []
    for candle_start, candle_rows in sorted(grouped.items()):
        candle_rows.sort(key=lambda x: x["open_time"])

        if len(candle_rows) != 15:
            continue

        expected_times = [
            candle_start + i * 60_000 for i in range(15)
        ]
        if [r["open_time"] for r in candle_rows] != expected_times:
            continue

        if not all(r.get("is_closed", False) for r in candle_rows):
            continue

        base = candle_rows[0]["open"]
        final_close = candle_rows[14]["close"]

        if final_close == base:
            actual = None
        else:
            actual = 1 if final_close > base else 0

        candles.append({
            "candle_start": candle_start,
            "rows": candle_rows,
            "actual": actual,
        })

    return candles


def build_observations(candles):
    observations = []

    for candle in candles:
        if candle["actual"] is None:
            continue

        rows = candle["rows"]
        base = rows[0]["open"]
        actual = candle["actual"]

        for minute in range(1, 14):
            current = rows[minute - 1]["close"]
            move = pct_change(base, current)

            observations.append({
                "candle_start": candle["candle_start"],
                "minute": minute,
                "move_bucket": move_bucket(move),
                "move": move,
                "actual": actual,
            })

    return observations


def model_key(obs):
    return obs["minute"], obs["move_bucket"]


def fit_model(train):
    stats = {}
    for obs in train:
        key = model_key(obs)
        item = stats.setdefault(key, [0, 0])
        item[0] += 1
        item[1] += obs["actual"]

    probabilities = {}
    for key, (n, wins) in stats.items():
        if n >= MIN_TRAIN_SAMPLES:
            probabilities[key] = wins / n

    return probabilities, stats


def bootstrap_block_ci(selected):
    by_candle = {}
    for item in selected:
        by_candle.setdefault(item["candle_start"], []).append(
            1 if item["won"] else 0
        )

    if len(by_candle) < 2:
        return None

    candle_scores = [
        sum(values) / len(values)
        for values in by_candle.values()
    ]

    rng = random.Random(42)
    means = []
    n = len(candle_scores)

    for _ in range(BOOTSTRAP_SAMPLES):
        sample = [candle_scores[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    low = means[int(0.025 * (len(means) - 1))]
    high = means[int(0.975 * (len(means) - 1))]

    return {
        "unique_candles": n,
        "win_rate_ci95_low": round(low * 100, 2),
        "win_rate_ci95_high": round(high * 100, 2),
    }


def baseline_on_same_signals(test, selected_keys):
    if not selected_keys:
        return None

    baseline = []
    for obs in test:
        key = (obs["candle_start"], obs["minute"])
        if key not in selected_keys:
            continue
        predicted = 1 if obs["move"] > 0 else 0
        baseline.append(predicted == obs["actual"])

    return round(sum(baseline) / len(baseline) * 100, 2) if baseline else None


def evaluate(test, probabilities, threshold):
    selected = []

    for obs in test:
        probability = probabilities.get(model_key(obs))
        if probability is None:
            continue

        side = None
        if probability >= threshold:
            side = 1
        elif probability <= 1.0 - threshold:
            side = 0

        if side is None:
            continue

        selected.append({
            **obs,
            "predicted": side,
            "won": side == obs["actual"],
        })

    wins = sum(1 for item in selected if item["won"])
    unique_candles = len({item["candle_start"] for item in selected})
    selected_keys = {
        (item["candle_start"], item["minute"])
        for item in selected
    }

    ci = bootstrap_block_ci(selected)

    return {
        "threshold": threshold,
        "signals": len(selected),
        "trades": len(selected),
        "wins": wins,
        "win_rate": round(wins / len(selected) * 100.0, 2) if selected else None,
        "unique_candles": unique_candles,
        "baseline_same_signals": baseline_on_same_signals(test, selected_keys),
        "block_bootstrap_ci95": ci,
    }


def add_metric(accumulator, result):
    accumulator["signals"] += result["signals"]
    accumulator["wins"] += result["wins"]
    accumulator["unique_candles"] += result["unique_candles"]


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


def observations_in_range(observations, start_ms, end_ms):
    return [
        obs for obs in observations
        if start_ms <= obs["candle_start"] < end_ms
    ]


def main():
    rows = fetch_klines()
    candles = build_candles_15m(rows)
    observations = build_observations(candles)

    folds = build_fold_ranges(candles)
    all_results = {
        str(t): {"signals": 0, "wins": 0, "unique_candles": 0}
        for t in THRESHOLDS
    }
    fold_results = []

    for index, (train_start, train_end, test_end) in enumerate(folds, start=1):
        train = observations_in_range(observations, train_start, train_end)
        test = observations_in_range(observations, train_end, test_end)

        probabilities, raw_stats = fit_model(train)
        evaluations = [
            evaluate(test, probabilities, threshold)
            for threshold in THRESHOLDS
        ]

        fold_results.append({
            "fold": index,
            "train_start": datetime.fromtimestamp(train_start / 1000, timezone.utc).isoformat(),
            "train_end": datetime.fromtimestamp(train_end / 1000, timezone.utc).isoformat(),
            "test_end": datetime.fromtimestamp(test_end / 1000, timezone.utc).isoformat(),
            "train_observations": len(train),
            "test_observations": len(test),
            "train_state_count": len(raw_stats),
            "usable_state_count": len(probabilities),
            "evaluations": evaluations,
        })

        for result in evaluations:
            add_metric(all_results[str(result["threshold"])], result)

    aggregate_evaluations = []
    for threshold in THRESHOLDS:
        item = all_results[str(threshold)]
        aggregate_evaluations.append({
            "threshold": threshold,
            "signals": item["signals"],
            "trades": item["signals"],
            "wins": item["wins"],
            "win_rate": round(
                item["wins"] / item["signals"] * 100.0, 2
            ) if item["signals"] else None,
            "unique_candles_sum": item["unique_candles"],
        })

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "days": DAYS,
        "method": "walk_forward",
        "train_days": TRAIN_DAYS,
        "test_days": TEST_DAYS,
        "step_days": STEP_DAYS,
        "min_train_samples": MIN_TRAIN_SAMPLES,
        "samples_1m": len(rows),
        "candles_15m": len(candles),
        "observations": len(observations),
        "folds": len(fold_results),
        "model": "minute_from_15m_open + current_move_bucket",
        "evaluations": aggregate_evaluations,
        "fold_results": fold_results,
        "notes": [
            "Only complete consecutive closed 15m candles are evaluated.",
            "Flat outcomes are excluded.",
            "Signals from the same 15m candle share one settlement outcome; block bootstrap is reported by candle.",
            "Baseline accuracy is measured on exactly the same selected signals.",
            "This remains a direction study, not a Polymarket P&L backtest."
        ],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)

    print(f"Reporte: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
