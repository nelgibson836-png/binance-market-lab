import json
import os
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

BASE_URL = "https://data-api.binance.vision"


def get_json(path):
    url = BASE_URL + path

    request = Request(
        url,
        headers={
            "User-Agent": "binance-market-lab/2.1"
        }
    )

    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def get_klines(symbol, interval):
    data = get_json(
        f"/api/v3/klines?symbol={symbol}&interval={interval}&limit=1"
    )

    k = data[0]

    return {
        "open_time": k[0],
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
        "volume": float(k[5]),
        "close_time": k[6],
        "trades": int(k[8])
    }


def collect_symbol(symbol):

    collected_at = datetime.now(timezone.utc).isoformat()

    ticker = get_json(
        f"/api/v3/ticker/24hr?symbol={symbol}"
    )

    book = get_json(
        f"/api/v3/ticker/bookTicker?symbol={symbol}"
    )

    bid = float(book["bidPrice"])
    ask = float(book["askPrice"])

    spread = ask - bid

    spread_percent = (
        (spread / bid) * 100
        if bid > 0
        else 0
    )

    candle_1m = get_klines(symbol, "1m")
    candle_5m = get_klines(symbol, "5m")
    candle_15m = get_klines(symbol, "15m")

    return {
        "collector_version": "2.1",

        "collected_at": collected_at,

        "exchange": "binance",

        "symbol": symbol,

        "spot": {
            "price": float(ticker["lastPrice"]),

            "volume_24h": float(
                ticker["volume"]
            ),

            "price_change_percent_24h": float(
                ticker["priceChangePercent"]
            ),

            "bid": bid,
            "ask": ask,

            "bid_qty": float(
                book["bidQty"]
            ),

            "ask_qty": float(
                book["askQty"]
            ),

            "spread": spread,

            "spread_percent": spread_percent,

            "exchange_timestamp": ticker["closeTime"]
        },

        "ohlcv": {
            "1m": candle_1m,
            "5m": candle_5m,
            "15m": candle_15m
        }
    }


def collect():

    print("=" * 50)
    print(" Binance Market Lab Collector v2.1")
    print("=" * 50)

    os.makedirs("data", exist_ok=True)

    output_file = "data/market_data.jsonl"

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

                data = collect_symbol(symbol)

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
                    f"Error consultando {symbol}: {error}"
                )

            except Exception as error:

                print(
                    f"Error inesperado en {symbol}: {error}"
                )


if __name__ == "__main__":
    collect()
