import unittest

import challenger_execution_v1 as challenger


class ChallengerExecutionTests(unittest.TestCase):
    def test_trade_uses_next_minute_open(self):
        start = 0
        rows = []
        for i in range(15):
            rows.append({
                "open_time": start + i * 60_000,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.0 + i,
                "close_time": start + i * 60_000 + 59_999,
                "is_closed": True,
            })

        candle = {
            "start": start,
            "rows": rows,
            "open": rows[0]["open"],
            "close": rows[-1]["close"],
        }

        trade = challenger.trade_for(candle, 5, 0)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["entry"], rows[5]["open"])
        self.assertEqual(trade["exit"], rows[14]["close"])

    def test_threshold_filters_small_moves(self):
        rows = []
        for i in range(15):
            rows.append({
                "open_time": i * 60_000,
                "open": 100.0,
                "high": 100.1,
                "low": 99.9,
                "close": 100.01 if i < 4 else 100.02,
                "close_time": i * 60_000 + 59_999,
                "is_closed": True,
            })

        candle = {
            "start": 0,
            "rows": rows,
            "open": 100.0,
            "close": rows[-1]["close"],
        }

        self.assertIsNone(challenger.trade_for(candle, 5, 30))


if __name__ == "__main__":
    unittest.main()
