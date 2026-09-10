import json
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://data-api.binance.vision"
SYMBOL = "BTCUSDT"

# ============================================================
# CONFIGURACIÓN
# ============================================================

DAYS = 30
INTERVAL = "1m"
LIMIT = 1000

OUTPUT_FILE = "data/strategy_probe.json"

# Umbrales de momentum que vamos a probar
MOMENTUM_WINDOWS = [1, 3, 5]
MOMENTUM_THRESHOLDS = [0.05, 0.10, 0.20, 0.30, 0.50]

# ============================================================
# HTTP
# ============================================================

def get_json(path):
    request = Request(
        BASE_URL + path,
        headers={
            "User-Agent": "binance-market-lab/strategy-probe"
        }
    )

    with urlopen(request, timeout=20) as response:
        return json.loads(
            response.read().decode("utf-8")
        )

# ============================================================
# DESCARGAR HISTÓRICO
# ============================================================

def fetch_klines():
    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    start_ms = now_ms - (
        DAYS * 24 * 60 * 60 * 1000
    )

    rows = []
    current_start = start_ms

    print("=" * 60)
    print("STRATEGY PROBE")
    print("=" * 60)
    print(f"Símbolo : {SYMBOL}")
    print(f"Periodo : {DAYS} días")
    print(f"Intervalo: {INTERVAL}")
    print()

    while current_start < now_ms:

        path = (
            f"/api/v3/klines"
            f"?symbol={SYMBOL}"
            f"&interval={INTERVAL}"
            f"&startTime={current_start}"
            f"&endTime={now_ms}"
            f"&limit={LIMIT}"
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
                "close_time": int(k[6]),
                "trades": int(k[8]),
            })

        last_open_time = int(data[-1][0])

        next_start = (
            last_open_time + 60_000
        )

        if next_start <= current_start:
            break

        current_start = next_start

        print(
            f"Velas descargadas: {len(rows)}"
        )

        time.sleep(0.08)

    # eliminar duplicados
    unique = {
        row["open_time"]: row
        for row in rows
    }

    rows = sorted(
        unique.values(),
        key=lambda x: x["open_time"]
    )

    print()
    print(f"Total velas: {len(rows)}")

    return rows

# ============================================================
# UTILIDADES
# ============================================================

def pct_change(a, b):
    if a == 0:
        return 0.0

    return (
        (b - a) / a
    ) * 100.0


def direction(value):
    if value > 0:
        return "UP"

    if value < 0:
        return "DOWN"

    return "FLAT"

# ============================================================
# ANÁLISIS DE MOMENTUM
# ============================================================

def analyze_momentum(rows):

    results = {}

    closes = [
        row["close"]
        for row in rows
    ]

    for window in MOMENTUM_WINDOWS:

        for threshold in MOMENTUM_THRESHOLDS:

            for side in ["UP", "DOWN"]:

                key = (
                    f"{window}m_"
                    f"{side}_"
                    f"{threshold:.2f}%"
                )

                samples = 0

                wins_1m = 0
                wins_3m = 0
                wins_5m = 0
                wins_15m = 0

                return_1m = []
                return_3m = []
                return_5m = []
                return_15m = []

                for i in range(
                    window,
                    len(rows) - 15
                ):

                    momentum = pct_change(
                        closes[i - window],
                        closes[i]
                    )

                    condition = False

                    if side == "UP":
                        condition = (
                            momentum >= threshold
                        )

                    elif side == "DOWN":
                        condition = (
                            momentum <= -threshold
                        )

                    if not condition:
                        continue

                    samples += 1

                    r1 = pct_change(
                        closes[i],
                        closes[i + 1]
                    )

                    r3 = pct_change(
                        closes[i],
                        closes[i + 3]
                    )

                    r5 = pct_change(
                        closes[i],
                        closes[i + 5]
                    )

                    r15 = pct_change(
                        closes[i],
                        closes[i + 15]
                    )

                    return_1m.append(r1)
                    return_3m.append(r3)
                    return_5m.append(r5)
                    return_15m.append(r15)

                    if side == "UP":
                        if r1 > 0:
                            wins_1m += 1
                        if r3 > 0:
                            wins_3m += 1
                        if r5 > 0:
                            wins_5m += 1
                        if r15 > 0:
                            wins_15m += 1

                    else:
                        if r1 < 0:
                            wins_1m += 1
                        if r3 < 0:
                            wins_3m += 1
                        if r5 < 0:
                            wins_5m += 1
                        if r15 < 0:
                            wins_15m += 1

                if samples == 0:
                    continue

                results[key] = {
                    "window_minutes": window,
                    "side": side,
                    "threshold_percent": threshold,
                    "samples": samples,

                    "win_rate_1m": round(
                        wins_1m / samples * 100,
                        2
                    ),

                    "win_rate_3m": round(
                        wins_3m / samples * 100,
                        2
                    ),

                    "win_rate_5m": round(
                        wins_5m / samples * 100,
                        2
                    ),

                    "win_rate_15m": round(
                        wins_15m / samples * 100,
                        2
                    ),

                    "avg_return_1m": round(
                        sum(return_1m) / len(return_1m),
                        5
                    ),

                    "avg_return_3m": round(
                        sum(return_3m) / len(return_3m),
                        5
                    ),

                    "avg_return_5m": round(
                        sum(return_5m) / len(return_5m),
                        5
                    ),

                    "avg_return_15m": round(
                        sum(return_15m) / len(return_15m),
                        5
                    )
                }

    return results

# ============================================================
# POLYMARKET-LIKE 15M ANALYSIS
# ============================================================

def analyze_15m_direction(rows):

    buckets = {}

    for row in rows:

        open_time = row["open_time"]

        # Inicio de la vela de 15 minutos
        candle_start = (
            open_time // 900_000
        ) * 900_000

        # Buscar índice de la vela de 15m
        # usando división temporal
        minute_from_start = (
            open_time - candle_start
        ) // 60_000

        if minute_from_start < 1:
            continue

        if minute_from_start > 14:
            continue

        key = f"minute_{minute_from_start}"

        if key not in buckets:
            buckets[key] = []

        buckets[key].append(row)

    results = {}

    # Construimos mapa open_time
    row_map = {
        row["open_time"]: row
        for row in rows
    }

    grouped = {}

    for row in rows:

        candle_start = (
            row["open_time"] // 900_000
        ) * 900_000

        grouped.setdefault(
            candle_start,
            []
        ).append(row)

    for minute in range(1, 15):

        samples = 0
        correct = 0

        for candle_start, candle_rows in grouped.items():

            candle_rows = sorted(
                candle_rows,
                key=lambda x: x["open_time"]
            )

            if len(candle_rows) < 15:
                continue

            base = candle_rows[0]["open"]

            current = candle_rows[minute]["close"]

            final_close = candle_rows[14]["close"]

            current_move = pct_change(
                base,
                current
            )

            if current_move == 0:
                continue

            actual = (
                "UP"
                if final_close > base
                else "DOWN"
            )

            predicted = (
                "UP"
                if current_move > 0
                else "DOWN"
            )

            samples += 1

            if predicted == actual:
                correct += 1

        if samples > 0:
            results[f"minute_{minute}"] = {
                "minute_from_15m_open": minute,
                "samples": samples,
                "direction_accuracy": round(
                    correct / samples * 100,
                    2
                )
            }

    return results

# ============================================================
# MAIN
# ============================================================

def main():

    rows = fetch_klines()

    print()
    print("Analizando momentum...")

    momentum = analyze_momentum(rows)

    print("Analizando dirección 15m...")

    direction_15m = analyze_15m_direction(rows)

    output = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "symbol": SYMBOL,
        "interval": INTERVAL,
        "days": DAYS,
        "samples": len(rows),

        "momentum": momentum,

        "direction_15m": direction_15m
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            indent=2,
            ensure_ascii=False
        )

    print()
    print("=" * 60)
    print("RESULTADOS DESTACADOS")
    print("=" * 60)

    ranked = sorted(
        momentum.items(),
        key=lambda x: (
            x[1]["win_rate_15m"],
            x[1]["samples"]
        ),
        reverse=True
    )

    shown = 0

    for key, result in ranked:

        if result["samples"] < 100:
            continue

        print(
            f"{key:18} "
            f"n={result['samples']:5} "
            f"15m={result['win_rate_15m']:6.2f}% "
            f"avg15={result['avg_return_15m']:8.4f}%"
        )

        shown += 1

        if shown >= 15:
            break

    print()
    print(
        f"Reporte guardado en {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
