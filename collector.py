import json
import os
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ============================================================
# Binance Market Lab
# Collector V3.0
# ============================================================

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
]

BASE_URL = "https://data-api.binance.vision"

COLLECTOR_VERSION = "3.0"


# ============================================================
# HTTP
# ============================================================

def get_json(path):
    url = BASE_URL + path

    request = Request(
        url,
        headers={
            "User-Agent": f"binance-market-lab/{COLLECTOR_VERSION}"
        }
    )

    with urlopen(request, timeout=15) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


# ============================================================
# KLINES
# ============================================================

def get_kline(symbol, interval):
    data = get_json(
        f"/api/v3/klines"
        f"?symbol={symbol}"
        f"&interval={interval}"
        f"&limit=2"
    )

    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    candles = []

    for k in data:

        open_time = int(k[0])
        close_time = int(k[6])

        candle = {
            "open_time": open_time,
            "close_time": close_time,

            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),

            "volume": float(k[5]),

            "trades": int(k[8]),

            "is_closed": close_time < now_ms
        }

        candles.append(candle)

    # La última vela es la más reciente.
    current = candles[-1]

    # También conservamos la última vela cerrada.
    closed = None

    for candle in reversed(candles):
        if candle["is_closed"]:
            closed = candle
            break

    return {
        "current": current,
        "last_closed": closed
    }


# ============================================================
# MARKET DATA
# ============================================================

def collect_symbol(symbol):

    collected_at = datetime.now(
        timezone.utc
    )

    collected_at_ms = int(
        collected_at.timestamp() * 1000
    )

    # --------------------------------------------------------
    # Ticker 24h
    # --------------------------------------------------------

    ticker = get_json(
        f"/api/v3/ticker/24hr?symbol={symbol}"
    )

    # --------------------------------------------------------
    # Best bid / ask
    # --------------------------------------------------------

    book = get_json(
        f"/api/v3/ticker/bookTicker?symbol={symbol}"
    )

    bid = float(book["bidPrice"])
    ask = float(book["askPrice"])

    bid_qty = float(book["bidQty"])
    ask_qty = float(book["askQty"])

    spread = ask - bid

    spread_percent = (
        (spread / bid) * 100
        if bid > 0
        else 0
    )

    # --------------------------------------------------------
    # Order book imbalance
    # --------------------------------------------------------

    total_qty = bid_qty + ask_qty

    if total_qty > 0:
        orderbook_imbalance = (
            (bid_qty - ask_qty)
            / total_qty
        )
    else:
        orderbook_imbalance = 0

    # --------------------------------------------------------
    # OHLCV
    # --------------------------------------------------------

    candle_1m = get_kline(
        symbol,
        "1m"
    )

    candle_5m = get_kline(
        symbol,
        "5m"
    )

    candle_15m = get_kline(
        symbol,
        "15m"
    )

    # --------------------------------------------------------
    # Data
    # --------------------------------------------------------

    return {

        "collector_version": COLLECTOR_VERSION,

        "collected_at": collected_at.isoformat(),

        "collected_at_ms": collected_at_ms,

        "exchange": "binance",

        "symbol": symbol,

        "spot": {

            "price": float(
                ticker["lastPrice"]
            ),

            "volume_24h": float(
                ticker["volume"]
            ),

            "price_change_percent_24h": float(
                ticker["priceChangePercent"]
            ),

            "bid": bid,

            "ask": ask,

            "bid_qty": bid_qty,

            "ask_qty": ask_qty,

            "spread": round(
                spread,
                10
            ),

            "spread_percent": round(
                spread_percent,
                10
            ),

            "orderbook_imbalance": round(
                orderbook_imbalance,
                6
            ),

            "exchange_timestamp": int(
                ticker["closeTime"]
            )
        },

        "ohlcv": {

            "1m": candle_1m,

            "5m": candle_5m,

            "15m": candle_15m
        }
    }


# ============================================================
# COLLECTOR
# ============================================================

def collect():

    print("=" * 60)

    print(
        " Binance Market Lab Collector V3.0"
    )

    print("=" * 60)

    print(
        "UTC:",
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    os.makedirs(
        "data",
        exist_ok=True
    )

    output_file = (
        "data/market_data.jsonl"
    )

    with open(
        output_file,
        "a",
        encoding="utf-8"
    ) as file:

        for symbol in SYMBOLS:

            try:

                print(
                    f"\nConsultando {symbol}..."
                )

                data = collect_symbol(
                    symbol
                )

                file.write(
                    json.dumps(
                        data,
                        ensure_ascii=False
                    ) + "\n"
                )

                file.flush()

                print(
                    json.dumps(
                        data,
                        indent=2,
                        ensure_ascii=False
                    )
                )

                time.sleep(0.5)

            except (
                HTTPError,
                URLError,
                TimeoutError
            ) as error:

                print(
                    f"Error consultando "
                    f"{symbol}: {error}"
                )

            except Exception as error:

                print(
                    f"Error inesperado en "
                    f"{symbol}: {error}"
                )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    collect()
