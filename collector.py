import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

BASE_URL = "https://data-api.binance.vision"
FUTURES_URL = "https://fapi.binance.com"

DATA_DIR = Path("data")
SPOT_DIR = DATA_DIR / "spot"
FUTURES_DIR = DATA_DIR / "futures"
ARBITRAGE_DIR = DATA_DIR / "arbitrage"

for directory in [SPOT_DIR, FUTURES_DIR, ARBITRAGE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


def get_json(base_url, endpoint):
    url = base_url + endpoint

    request = Request(
        url,
        headers={
            "User-Agent": "binance-market-lab/1.0"
        }
    )

    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def save_jsonl(path, data):
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, separators=(",", ":")) + "\n")


def collect_spot():
    print("Recopilando mercado Spot...")

    exchange_info = get_json(
        BASE_URL,
        "/api/v3/exchangeInfo"
    )

    symbols = []

    for symbol in exchange_info["symbols"]:
        if (
            symbol["status"] == "TRADING"
            and symbol["quoteAsset"] == "USDT"
            and symbol["isSpotTradingAllowed"]
        ):
            symbols.append(symbol["symbol"])

    print(f"Pares Spot USDT encontrados: {len(symbols)}")

    tickers = get_json(
        BASE_URL,
        "/api/v3/ticker/24hr"
    )

    ticker_map = {
        ticker["symbol"]: ticker
        for ticker in tickers
        if ticker["symbol"] in symbols
    }

    timestamp = datetime.now(timezone.utc).isoformat()

    count = 0

    for symbol in symbols:

        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        record = {
            "timestamp": timestamp,
            "symbol": symbol,
            "price": float(ticker["lastPrice"]),
            "bid": None,
            "ask": None,
            "volume": float(ticker["volume"]),
            "quote_volume": float(ticker["quoteVolume"]),
            "price_change_percent": float(
                ticker["priceChangePercent"]
            )
        }

        save_jsonl(
            SPOT_DIR / "market.jsonl",
            record
        )

        count += 1

    print(f"Datos Spot guardados: {count}")


def collect_futures():
    print("Recopilando Funding Futures...")

    funding = get_json(
        FUTURES_URL,
        "/fapi/v1/premiumIndex"
    )

    timestamp = datetime.now(timezone.utc).isoformat()

    count = 0

    for item in funding:

        symbol = item.get("symbol")

        if not symbol:
            continue

        record = {
            "timestamp": timestamp,
            "symbol": symbol,
            "mark_price": float(item["markPrice"]),
            "index_price": float(item["indexPrice"]),
            "funding_rate": float(item["lastFundingRate"]),
            "next_funding_time": item["nextFundingTime"]
        }

        save_jsonl(
            FUTURES_DIR / "funding.jsonl",
            record
        )

        count += 1

    print(f"Datos Funding guardados: {count}")


def calculate_triangular_arbitrage():

    print("Analizando arbitraje triangular...")

    tickers = get_json(
        BASE_URL,
        "/api/v3/ticker/bookTicker"
    )

    books = {
        item["symbol"]: {
            "bid": float(item["bidPrice"]),
            "ask": float(item["askPrice"])
        }
        for item in tickers
        if float(item["bidPrice"]) > 0
        and float(item["askPrice"]) > 0
    }

    routes = [
        ("BTCUSDT", "ETHBTC", "ETHUSDT"),
        ("BTCUSDT", "BNBBTC", "BNBUSDT"),
        ("BTCUSDT", "SOLBTC", "SOLUSDT"),
        ("ETHUSDT", "BNBETH", "BNBUSDT"),
        ("ETHUSDT", "SOLETH", "SOLUSDT"),
    ]

    timestamp = datetime.now(timezone.utc).isoformat()

    opportunities = 0

    fee = 0.001

    for leg1, leg2, leg3 in routes:

        if not all(
            symbol in books
            for symbol in [leg1, leg2, leg3]
        ):
            continue

        a = books[leg1]
        b = books[leg2]
        c = books[leg3]

        start = 1.0

        step1 = start / a["ask"]
        step1 *= 1 - fee

        step2 = step1 / b["ask"]
        step2 *= 1 - fee

        final = step2 * c["bid"]
        final *= 1 - fee

        profit_percent = (final - start) * 100

        record = {
            "timestamp": timestamp,
            "route": [
                leg1,
                leg2,
                leg3
            ],
            "initial": start,
            "final": final,
            "profit_percent": profit_percent,
            "profitable_after_fee": profit_percent > 0
        }

        save_jsonl(
            ARBITRAGE_DIR / "opportunities.jsonl",
            record
        )

        if profit_percent > 0:
            opportunities += 1

    print(
        f"Oportunidades triangulares positivas: "
        f"{opportunities}"
    )


def collect():

    print("=" * 60)
    print("BINANCE MARKET LAB")
    print("=" * 60)

    collect_spot()
    collect_futures()
    calculate_triangular_arbitrage()

    print("=" * 60)
    print("Ciclo terminado")
    print("=" * 60)


if __name__ == "__main__":
    collect()
