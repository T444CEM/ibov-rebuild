# Run with:  python -m unittest discover tests

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from ibov.__main__ import main
from ibov.b3 import br_number, parse_cotahist, parse_history, parse_portfolio
from ibov.calc import analyse
from ibov.report import checks
from tests.fixtures import cotahist_zip, history_json, portfolio_json

# A three-stock index where BBBB4 goes ex-dividend on PAYOUT_DATE
PREV_DATE = date(2026, 11, 10)
PAYOUT_DATE = date(2026, 11, 11)
QTY = {"AAAA3": 1_000_000, "BBBB4": 2_000_000, "CCCC3": 500_000}
PREV = {"AAAA3": 50.00, "BBBB4": 20.00, "CCCC3": 80.00}  # traded closes on PREV_DATE
TODAY = {"AAAA3": 51.00, "BBBB4": 19.20, "CCCC3": 80.40}  # traded closes on PAYOUT_DATE
PAYOUT = {"BBBB4": 0.60}
START_REDUTOR = 1_000.0


# What B3 would publish: the redutor is lowered so the payout does not move the index
def scenario() -> tuple[float, float, float, list[dict]]:
    adjusted_prices = {}
    prev_values = []
    adjusted_values = []
    today_values = []

    for ticker in QTY:
        adjusted_prices[ticker] = PREV[ticker] - PAYOUT.get(ticker, 0)
        prev_values.append(QTY[ticker] * PREV[ticker])
        adjusted_values.append(QTY[ticker] * adjusted_prices[ticker])
        today_values.append(QTY[ticker] * TODAY[ticker])

    value_prev = sum(prev_values)
    value_adjusted = sum(adjusted_values)
    value_today = sum(today_values)

    official_prev = value_prev / START_REDUTOR
    redutor = START_REDUTOR * value_adjusted / value_prev
    official = value_today / redutor

    lines = []

    for ticker in QTY:
        weight = round(100 * QTY[ticker] * adjusted_prices[ticker] / value_adjusted, 3)
        lines.append({"ticker": ticker, "name": ticker, "qty": QTY[ticker], "weight": weight})

    return official_prev, official, redutor, lines


class Parsing(unittest.TestCase):

    def test_br_number(self):
        self.assertEqual(br_number("13.980.694,43000744"), 13980694.43000744)
        self.assertEqual(br_number("9,975"), 9.975)

    def test_cotahist_roundtrip(self):
        rows = parse_cotahist(cotahist_zip(PREV_DATE, PREV))

        closes = {}

        for row in rows:
            closes[row["ticker"]] = row["close"]

        self.assertEqual(closes, PREV)
        self.assertEqual(rows[0]["trade_date"], PREV_DATE)

    def test_portfolio(self):
        _, _, redutor, lines = scenario()

        portfolio = parse_portfolio(portfolio_json(PAYOUT_DATE, redutor, lines))

        self.assertEqual(portfolio.portfolio_date, PAYOUT_DATE)
        self.assertAlmostEqual(portfolio.redutor, redutor, places=6)
        self.assertEqual(portfolio.check(), [])

    def test_history(self):
        content = history_json({PREV_DATE: 171336.99, PAYOUT_DATE: 172060.43})
        expected = {PREV_DATE: 171336.99, PAYOUT_DATE: 172060.43}

        self.assertEqual(parse_history(content, 2026), expected)


class Arithmetic(unittest.TestCase):

    def setUp(self):
        self.official_prev, self.official, redutor, lines = scenario()
        self.portfolio = parse_portfolio(portfolio_json(PAYOUT_DATE, redutor, lines))

    def run_analysis(self, session_date: date = PAYOUT_DATE, prev_date: date = PREV_DATE):
        return analyse(self.portfolio, TODAY, PREV, self.official, self.official_prev,
                       session_date=session_date, prev_date=prev_date)

    def test_level_reconciles_on_same_day_data(self):
        analysis = self.run_analysis()

        self.assertAlmostEqual(analysis.rebuilt, self.official, places=6)

    def test_payout_detected_from_weights(self):
        analysis = self.run_analysis()

        adjusted_tickers = []

        for line in analysis.adjusted:
            adjusted_tickers.append(line.ticker)

        self.assertEqual(adjusted_tickers, ["BBBB4"])
        self.assertAlmostEqual(analysis.adjusted[0].payout, 0.60, places=2)

    def test_contributions_add_up_to_official_change(self):
        analysis = self.run_analysis()
        expected = self.official - self.official_prev

        self.assertAlmostEqual(analysis.contribution_total, expected, delta=0.05)

    def test_all_acceptance_checks_pass_on_same_day_data(self):
        analysis = self.run_analysis()

        for name, result, passed in checks(analysis):
            self.assertTrue(passed, f"{name}: {result}")

    # Yesterday's prices on today's portfolio sit above yesterday's close by exactly the payout
    def test_gap_explained_by_named_payout(self):
        analysis = self.run_analysis()
        expected = QTY["BBBB4"] * PAYOUT["BBBB4"] / self.portfolio.redutor

        self.assertAlmostEqual(analysis.divisor_pts, expected, places=6)
        self.assertTrue(analysis.reconciled_payouts)
        self.assertAlmostEqual(analysis.adjustment_pts, analysis.divisor_pts, places=9)  # the gap is fully named

    # A portfolio from another session says nothing about this session's payouts
    def test_no_payouts_inferred_from_another_day_portfolio(self):
        analysis = self.run_analysis(session_date=date(2026, 11, 12), prev_date=PAYOUT_DATE)

        self.assertEqual(analysis.adjusted, [])
        self.assertIsNone(analysis.divisor_pts)

    # Using traded closes for BBBB4 would wrongly count the payout as a loss
    def test_without_payout_adjustment_change_is_wrong(self):
        naive_value = 0

        for ticker in QTY:
            naive_value += QTY[ticker] * (TODAY[ticker] - PREV[ticker])

        naive_change = naive_value / self.portfolio.redutor
        official_change = self.official - self.official_prev

        self.assertGreater(abs(naive_change - official_change), 1)


class EndToEnd(unittest.TestCase):

    def test_report_from_raw_files(self):
        official_prev, official, redutor, lines = scenario()

        with TemporaryDirectory() as directory:

            raw = Path(directory) / "raw"

            for folder in ("portfolio", "quotes", "history"):
                (raw / folder).mkdir(parents=True)

            (raw / "portfolio" / f"IBOV_{PAYOUT_DATE}.json").write_bytes(portfolio_json(PAYOUT_DATE, redutor, lines))
            (raw / "quotes" / "COTAHIST_D10112026.ZIP").write_bytes(cotahist_zip(PREV_DATE, PREV))
            (raw / "quotes" / "COTAHIST_D11112026.ZIP").write_bytes(cotahist_zip(PAYOUT_DATE, TODAY))
            (raw / "history" / "history_2026.json").write_bytes(
                history_json({PREV_DATE: official_prev, PAYOUT_DATE: official})
            )

            self.assertEqual(main(["--data", directory, "report"]), 0)

            report_html = (Path(directory) / "reports" / f"ibov_{PAYOUT_DATE}.html").read_text(encoding="utf-8")

            self.assertIn("Reconciled", report_html)
            self.assertIn("Checks", report_html)
            self.assertNotIn("class='fail'", report_html)
            self.assertIn("COTAHIST_D11112026.ZIP", report_html)  # numbers trace to named raw files
            self.assertIn("Payouts absorbed by the divisor", report_html)
            self.assertIn("What moved the index", report_html)
            self.assertTrue((Path(directory) / "reports" / f"ibov_{PAYOUT_DATE}.csv").exists())

    def test_report_with_nothing_stored(self):
        with TemporaryDirectory() as directory:

            self.assertEqual(main(["--data", directory, "report"]), 1)


if __name__ == "__main__":
    unittest.main()
