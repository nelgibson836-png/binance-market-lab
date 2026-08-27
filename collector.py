import json
import os
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
            "User-Agent": "binance-market-lab/1.0"
        }
    )

    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_symbol(symbol):
    ticker = get_json(
        f"/api/v3/ticker/24hr?symbol={symbol}"
    )

    book = get_json(
        f"/api/v3/ticker/bookTicker?symbol={symbol}"
    )

    bid = float(book["bidPrice"])
    ask = float(book["askPrice"])

    spread = ask - bid

    if bid > 0:
        spread_percent = (spread / bid) * 100
    else:
        spread_percent = 0

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "price": float(ticker["lastPrice"]),
        "volume_24h": float(ticker["volume"]),
        "price_change_percent": float(ticker["priceChangePercent"]),
        "bid": bid,
        "ask": ask,
        "bid_qty": float(book["bidQty"]),
        "ask_qty": float(book["askQty"]),
        "spread": spread,
        "spread_percent": spread_percent
    }

    return data


def collect():
    print("Consultando Binance...")

    os.makedirs("data", exist_ok=True)

    output_file = "data/market_data.jsonl"

    with open(output_file, "a", encoding="utf-8") as file:

        for symbol in SYMBOLS:

            try:
                data = collect_symbol(symbol)

                file.write(
                    json.dumps(data, ensure_ascii=False) + "\n"
                )

                print(json.dumps(data, indent=2))

            except (HTTPError, URLError, TimeoutError) as error:
                print(f"Error consultando {symbol}: {error}")

            except Exception as error:
                print(f"Error inesperado en {symbol}: {error}")


if __name__ == "__main__":
    collect()
