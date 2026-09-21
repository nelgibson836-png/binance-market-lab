import json
import os
from collections import defaultdict
from datetime import datetime, timezone


INPUT_FILE = "data/market_data_v4.jsonl"
OUTPUT_FILE = "data/quality_report.json"
EXPECTED_SCHEMA_VERSION = "4.0"


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_records():
    records = []
    if not os.path.exists(INPUT_FILE):
        return records

    with open(INPUT_FILE, "r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                data["_line"] = line_number
                records.append(data)
            except json.JSONDecodeError:
                print(f"JSON inválido en línea {line_number}")

    return records


def analyze(records):
    symbols = defaultdict(list)
    invalid_records = []
    legacy_records = []

    for record in records:
        if record.get("schema_version") != EXPECTED_SCHEMA_VERSION:
            legacy_records.append(record.get("_line"))
            continue

        symbol = record.get("symbol")
        collected_at = record.get("collected_at")
        if not symbol or not collected_at:
            invalid_records.append(record.get("_line"))
            continue

        symbols[symbol].append(record)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "input_file": INPUT_FILE,
        "total_records": len(records),
        "legacy_records": legacy_records,
        "invalid_records": invalid_records,
        "symbols": {},
    }

    for symbol, rows in symbols.items():
        rows.sort(key=lambda x: parse_time(x["collected_at"]))

        timestamps = [parse_time(row["collected_at"]) for row in rows]
        intervals = [
            (timestamps[i] - timestamps[i - 1]).total_seconds()
            for i in range(1, len(timestamps))
        ]

        average_interval = sum(intervals) / len(intervals) if intervals else None
        min_interval = min(intervals) if intervals else None
        max_interval = max(intervals) if intervals else None

        current_candles_closed = 0
        current_candles_open = 0
        missing_last_closed = 0

        for row in rows:
            for interval in ("1m", "5m", "15m"):
                candle = row.get("ohlcv", {}).get(interval, {})
                current = candle.get("current")
                last_closed = candle.get("last_closed")

                if current:
                    if current.get("is_closed"):
                        current_candles_closed += 1
                    else:
                        current_candles_open += 1

                if not last_closed:
                    missing_last_closed += 1

        report["symbols"][symbol] = {
            "records": len(rows),
            "first_record": timestamps[0].isoformat() if timestamps else None,
            "last_record": timestamps[-1].isoformat() if timestamps else None,
            "average_interval_seconds": round(average_interval, 2) if average_interval is not None else None,
            "minimum_interval_seconds": min_interval,
            "maximum_interval_seconds": max_interval,
            "duplicate_timestamps": len(timestamps) - len(set(timestamps)),
            "current_candles_closed": current_candles_closed,
            "current_candles_open": current_candles_open,
            "missing_last_closed": missing_last_closed,
        }

    return report


def main():
    records = load_records()
    report = analyze(records)

    os.makedirs("data", exist_ok=True)
    temp_file = OUTPUT_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    os.replace(temp_file, OUTPUT_FILE)

    print("=" * 60)
    print(" Binance Market Lab")
    print(" Data Quality Report V4")
    print("=" * 60)
    print(f"Total records: {report['total_records']}")
    print(f"Legacy records excluded: {len(report['legacy_records'])}")
    print(f"Invalid records: {len(report['invalid_records'])}")

    for symbol, data in report["symbols"].items():
        print(
            f"{symbol}: {data['records']} records | "
            f"avg={data['average_interval_seconds']}s | "
            f"max_gap={data['maximum_interval_seconds']}s"
        )

    print(f"Report saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
