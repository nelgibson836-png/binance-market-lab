import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

BASE_URL = "https://data-api.binance.vision"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)


def get_json(endpoint):
    url = BASE_URL + endpoint
    request = Request(
        url,
        headers={"User-Agent": "binance-market-lab/1.0"}
    )

    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def collect():
    timestamp = datetime.now(timezone.utc).isoformat()

    print("Consultando Binance...")

    ticker = get_json("/api/v3/ticker/24hr?symbol=BTCUSDT")
    book = get_json("/api/v3/ticker/bookTicker?symbol=BTCUSDT")

    data = {
        "timestamp": timestamp,
        "symbol": "BTCUSDT",
        "price": float(ticker["lastPrice"]),
        "volume_24h": float(ticker["volume"]),
        "price_change_percent": float(ticker["priceChangePercent"]),
        "bid": float(book["bidPrice"]),
        "ask": float(book["askPrice"]),
        "bid_qty": float(book["bidQty"]),
        "ask_qty": float(book["askQty"])
    }

    filename = DATA_DIR / "market_data.jsonl"

    with filename.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data) + "\n")

    print("Datos guardados:")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    collect()
