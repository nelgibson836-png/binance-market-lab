import json
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://data-api.binance.vision"
SYMBOL = "BTCUSDT"

DAYS = 30
INTERVAL = "1m"
LIMIT = 1000
OUTPUT_FILE = "data/strategy_probe.json"

MOMENTUM_WINDOWS = [1, 3, 5]
MOMENTUM_THRESHOLDS = [0.05, 0.10, 0.20, 0.30, 0.50]


def get_json(path):
    request = Request(
        BASE_URL + path,
        headers={"User-Agent": "binance-market-lab/strategy-probe"},
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
    rows = sorted(unique.values(), key=lambda x: x["open_time"])
    print(f"Total velas cerradas: {len(rows)}")
    return rows


def pct_change(a, b):
    return 0.0 if a == 0 else ((b - a) / a) * 100.0


def analyze_momentum(rows):
    results = {}
    closes = [row["close"] for row in rows]

    for window in MOMENTUM_WINDOWS:
        for threshold in MOMENTUM_THRESHOLDS:
            for side in ["UP", "DOWN"]:
                key = f"{window}m_{side}_{threshold:.2f}%"

                samples = 0
                wins = {1: 0, 3: 0, 5: 0, 15: 0}
                returns = {1: [], 3: [], 5: [], 15: []}
                strategy_returns = {1: [], 3: [], 5: [], 15: []}

                for i in range(window, len(rows) - 15):
                    momentum = pct_change(closes[i - window], closes[i])
                    condition = (
                        momentum >= threshold
                        if side == "UP"
                        else momentum <= -threshold
                    )
                    if not condition:
                        continue

                    samples += 1
                    for horizon in (1, 3, 5, 15):
                        r = pct_change(closes[i], closes[i + horizon])
                        returns[horizon].append(r)
                        strategy_r = r if side == "UP" else -r
                        strategy_returns[horizon].append(strategy_r)
                        if strategy_r > 0:
                            wins[horizon] += 1

                if samples == 0:
                    continue

                result = {
                    "window_minutes": window,
                    "side": side,
                    "threshold_percent": threshold,
                    "samples": samples,
                }

                for horizon in (1, 3, 5, 15):
                    result[f"win_rate_{horizon}m"] = round(
                        wins[horizon] / samples * 100, 2
                    )
                    result[f"avg_return_{horizon}m"] = round(
                        sum(returns[horizon]) / samples, 5
                    )
                    result[f"avg_strategy_return_{horizon}m"] = round(
                        sum(strategy_returns[horizon]) / samples, 5
                    )

                results[key] = result

    return results


def analyze_15m_direction(rows):
    grouped = {}
    for row in rows:
        candle_start = (row["open_time"] // 900_000) * 900_000
        grouped.setdefault(candle_start, []).append(row)

    results = {}

    for minute in range(1, 14):
        samples = 0
        correct = 0

        for candle_start, candle_rows in grouped.items():
            candle_rows.sort(key=lambda x: x["open_time"])

            if len(candle_rows) != 15:
                continue

            expected_times = [
                candle_start + i * 60_000 for i in range(15)
            ]
            if [r["open_time"] for r in candle_rows] != expected_times:
                continue

            base = candle_rows[0]["open"]
            current = candle_rows[minute - 1]["close"]
            final_close = candle_rows[14]["close"]
            current_move = pct_change(base, current)

            if current_move == 0 or final_close == base:
                continue

            actual = "UP" if final_close > base else "DOWN"
            predicted = "UP" if current_move > 0 else "DOWN"

            samples += 1
            correct += predicted == actual

        if samples:
            results[f"minute_{minute}"] = {
                "minute_from_15m_open": minute,
                "samples": samples,
                "direction_accuracy": round(correct / samples * 100, 2),
            }

    return results


def main():
    rows = fetch_klines()
    momentum = analyze_momentum(rows)
    direction_15m = analyze_15m_direction(rows)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "days": DAYS,
        "samples": len(rows),
        "momentum": momentum,
        "direction_15m": direction_15m,
        "notes": [
            "Only closed 1m candles are used.",
            "15m minute=1 means the close of the first 1m candle.",
            "Minute 14 is excluded because it uses the final 15m close.",
            "Flat 15m outcomes are excluded instead of forced into DOWN.",
            "Strategy returns for DOWN signals invert the underlying return sign."
        ],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)

    print(f"Reporte guardado en {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
