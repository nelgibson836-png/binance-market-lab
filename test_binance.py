from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

TESTS = [
    ("SPOT DATA API", "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT"),
    ("BINANCE API", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
    ("FUTURES API", "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"),
    ("FUTURES OI", "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"),
]

for name, url in TESTS:
    print("\n" + "=" * 50)
    print(name)
    print(url)

    try:
        request = Request(
            url,
            headers={
                "User-Agent": "binance-market-lab-test/1.0"
            }
        )

        with urlopen(request, timeout=15) as response:
            data = response.read().decode("utf-8")

        print("RESULTADO: OK")
        print(data[:500])

    except HTTPError as e:
        print(f"RESULTADO: HTTP ERROR {e.code}")
        print(e)

    except URLError as e:
        print("RESULTADO: URL ERROR")
        print(e)

    except Exception as e:
        print("RESULTADO: ERROR")
        print(e)
