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
OUTPUT_FILE = "data/backtest_15m.json"
TRAIN_RATIO = 0.70
MIN_TRAIN_SAMPLES = 100

# Precio actual respecto a la apertura de la vela 15m.
MOVE_BINS = [
    -math.inf, -0.30, -0.20, -0.10, -0.05,
    0.00, 0.05, 0.10, 0.20, 0.30, math.inf
]


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

    print("=" * 60)
    print("BACKTEST 15M")
    print("=" * 60)
    print(f"Symbol   : {SYMBOL}")
    print(f"Periodo  : {DAYS} dias")
    print(f"Intervalo: {INTERVAL}")

    while current_start < now_ms:
        path = (
            f"/api/v3/klines?symbol={SYMBOL}"
            f"&interval={INTERVAL}&startTime={current_start}"
            f"&endTime={now_ms}&limit={LIMIT}"
        )
        try:
            data = get_json(path)
        except (HTTPError, URLError, TimeoutError) as error:
            print(f"Error descargando datos: {error}")
            raise

        if not data:
            break

        for k in data:
            rows.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "trades": int(k[8]),
            })

        last_open_time = int(data[-1][0])
        next_start = last_open_time + 60_000
        if next_start <= current_start:
            break
        current_start = next_start
        print(f"Velas: {len(rows)}")
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


def build_observations(rows):
    grouped = {}
    for row in rows:
        candle_start = (row["open_time"] // 900_000) * 900_000
        grouped.setdefault(candle_start, []).append(row)

    observations = []
    for candle_start, candle_rows in sorted(grouped.items()):
        candle_rows.sort(key=lambda x: x["open_time"])
        if len(candle_rows) < 15:
            continue

        base = candle_rows[0]["open"]
        final_close = candle_rows[14]["close"]
        actual = 1 if final_close > base else 0

        for minute in range(1, 14):
            current = candle_rows[minute]["close"]
            move = pct_change(base, current)
            m1 = pct_change(candle_rows[minute - 1]["close"], current)
            m3 = pct_change(candle_rows[max(0, minute - 3)]["close"], current)
            m5 = pct_change(candle_rows[max(0, minute - 5)]["close"], current)
            observations.append({
                "candle_start": candle_start,
                "minute": minute,
                "move_bucket": move_bucket(move),
                "move": move,
                "m1": m1,
                "m3": m3,
                "m5": m5,
                "actual": actual,
            })
    return observations


def model_key(obs):
    return (obs["minute"], obs["move_bucket"])


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


def baseline_accuracy(test):
    if not test:
        return 0.0
    correct = 0
    for obs in test:
        predicted = 1 if obs["move"] > 0 else 0
        correct += predicted == obs["actual"]
    return correct / len(test) * 100.0


def evaluate(test, probabilities, threshold):
    trades = 0
    wins = 0
    by_minute = {}

    for obs in test:
        key = model_key(obs)
        probability = probabilities.get(key)
        if probability is None:
            continue

        side = None
        if probability >= threshold:
            side = 1
        elif probability <= 1.0 - threshold:
            side = 0

        if side is None:
            continue

        trades += 1
        won = side == obs["actual"]
        wins += won

        bucket = by_minute.setdefault(str(obs["minute"]), [0, 0])
        bucket[0] += 1
        bucket[1] += won

    result = {
        "threshold": threshold,
        "trades": trades,
        "wins": wins,
        "win_rate": round(wins / trades * 100.0, 2) if trades else None,
        "by_minute": {
            minute: {
                "trades": values[0],
                "win_rate": round(values[1] / values[0] * 100.0, 2),
            }
            for minute, values in sorted(by_minute.items(), key=lambda x: int(x[0]))
        },
    }
    return result


def main():
    rows = fetch_klines()
    print(f"Total velas: {len(rows)}")

    observations = build_observations(rows)
    observations.sort(key=lambda x: x["candle_start"])
    split = int(len(observations) * TRAIN_RATIO)
    train = observations[:split]
    test = observations[split:]

    probabilities, raw_stats = fit_model(train)
    print(f"Observaciones: {len(observations)}")
    print(f"Train: {len(train)} | Test: {len(test)}")
    print(f"Estados aprendidos: {len(probabilities)}")
    print(f"Baseline test: {baseline_accuracy(test):.2f}%")

    evaluations = [evaluate(test, probabilities, threshold) for threshold in (0.55, 0.60, 0.65, 0.70)]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "days": DAYS,
        "train_ratio": TRAIN_RATIO,
        "min_train_samples": MIN_TRAIN_SAMPLES,
        "samples_1m": len(rows),
        "observations": len(observations),
        "train_observations": len(train),
        "test_observations": len(test),
        "baseline_test_accuracy": round(baseline_accuracy(test), 2),
        "model": "minute_from_15m_open + current_move_bucket",
        "evaluations": evaluations,
        "train_state_count": len(raw_stats),
        "usable_state_count": len(probabilities),
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)

    print("\nRESULTADOS")
    print("=" * 60)
    for result in evaluations:
        print(
            f"threshold={result['threshold']:.2f} "
            f"trades={result['trades']:5} "
            f"win_rate={str(result['win_rate']):>6}%"
        )
    print(f"Reporte: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
