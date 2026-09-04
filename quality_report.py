import json
import os
from collections import defaultdict
from datetime import datetime, timezone


INPUT_FILE = "data/market_data.jsonl"
OUTPUT_FILE = "data/quality_report.json"


def parse_time(value):
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def load_records():

    records = []

    if not os.path.exists(INPUT_FILE):
        return records

    with open(
        INPUT_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        for line_number, line in enumerate(
            file,
            start=1
        ):

            line = line.strip()

            if not line:
                continue

            try:

                data = json.loads(line)

                data["_line"] = line_number

                records.append(data)

            except json.JSONDecodeError:

                print(
                    f"JSON inválido "
                    f"en línea {line_number}"
                )

    return records


def analyze(records):

    symbols = defaultdict(list)

    invalid_records = []

    for record in records:

        symbol = record.get("symbol")

        collected_at = record.get(
            "collected_at"
        )

        if not symbol or not collected_at:

            invalid_records.append(
                record.get("_line")
            )

            continue

        symbols[symbol].append(
            record
        )

    report = {

        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "total_records": len(records),

        "symbols": {},

        "invalid_records": invalid_records
    }

    for symbol, rows in symbols.items():

        rows.sort(
            key=lambda x: parse_time(
                x["collected_at"]
            )
        )

        timestamps = [
            parse_time(
                row["collected_at"]
            )
            for row in rows
        ]

        intervals = []

        duplicates = 0

        seen_timestamps = set()

        for timestamp in timestamps:

            if timestamp in seen_timestamps:

                duplicates += 1

            seen_timestamps.add(timestamp)

        for i in range(
            1,
            len(timestamps)
        ):

            delta = (
                timestamps[i]
                - timestamps[i - 1]
            ).total_seconds()

            intervals.append(delta)

        if intervals:

            average_interval = (
                sum(intervals)
                / len(intervals)
            )

            min_interval = min(
                intervals
            )

            max_interval = max(
                intervals
            )

        else:

            average_interval = None
            min_interval = None
            max_interval = None

        closed_candles = 0
        open_candles = 0

        for row in rows:

            for interval in (
                "1m",
                "5m",
                "15m"
            ):

                candle = (
                    row
                    .get("ohlcv", {})
                    .get(interval, {})
                    .get("current")
                )

                if candle:

                    if candle.get(
                        "is_closed"
                    ):

                        closed_candles += 1

                    else:

                        open_candles += 1

        report["symbols"][symbol] = {

            "records": len(rows),

            "first_record": (
                timestamps[0].isoformat()
                if timestamps
                else None
            ),

            "last_record": (
                timestamps[-1].isoformat()
                if timestamps
                else None
            ),

            "average_interval_seconds": (
                round(
                    average_interval,
                    2
                )
                if average_interval
                is not None
                else None
            ),

            "minimum_interval_seconds": (
                min_interval
                if min_interval is not None
                else None
            ),

            "maximum_interval_seconds": (
                max_interval
                if max_interval is not None
                else None
            ),

            "duplicate_timestamps": duplicates,

            "current_candles_closed": (
                closed_candles
            ),

            "current_candles_open": (
                open_candles
            )
        }

    return report


def main():

    records = load_records()

    report = analyze(records)

    os.makedirs(
        "data",
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            report,
            file,
            indent=2,
            ensure_ascii=False
        )

    print("=" * 60)

    print(
        " Binance Market Lab"
    )

    print(
        " Data Quality Report"
    )

    print("=" * 60)

    print()

    print(
        "Total records:",
        report["total_records"]
    )

    for symbol, data in report[
        "symbols"
    ].items():

        print()

        print(
            symbol,
            "->",
            data["records"],
            "records"
        )

        print(
            "  Average interval:",
            data[
                "average_interval_seconds"
            ],
            "seconds"
        )

        print(
            "  Maximum gap:",
            data[
                "maximum_interval_seconds"
            ],
            "seconds"
        )

        print(
            "  Duplicate timestamps:",
            data[
                "duplicate_timestamps"
            ]
        )

    print()

    print(
        "Report saved to:",
        OUTPUT_FILE
    )


if __name__ == "__main__":
    main()
