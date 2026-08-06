import unittest

import pandas as pd

from backtester import apply_portfolio_constraints, build_swing_signals, is_swing_entry_at
from config import correlation_group


def trade(symbol, entry_date, exit_date, entry_price, exit_price):
    return {
        "symbol": symbol,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "estimated_option_entry_price": entry_price,
        "estimated_option_exit_price": exit_price,
    }


class PortfolioConstraintTests(unittest.TestCase):
    def test_allows_two_concurrent_positions_and_rejects_third(self):
        candidates = [
            trade("A", "2026-01-01", "2026-01-03", 0.75, 1.00),
            trade("B", "2026-01-01", "2026-01-02", 0.75, 0.50),
            trade("C", "2026-01-01", "2026-01-02", 0.25, 0.50),
        ]

        selected = apply_portfolio_constraints(
            candidates,
            starting_cash=2500,
            max_positions=2,
            max_total_premium=200,
            max_entry_premium=100,
        )

        self.assertEqual([item["symbol"] for item in selected], ["A", "B"])

    def test_releases_cash_after_exit_for_a_later_entry(self):
        candidates = [
            trade("A", "2026-01-01", "2026-01-02", 1.00, 0.50),
            trade("B", "2026-01-03", "2026-01-04", 0.50, 0.75),
        ]

        selected = apply_portfolio_constraints(
            candidates,
            starting_cash=100,
            max_positions=2,
            max_total_premium=200,
            max_entry_premium=100,
        )

        self.assertEqual([item["symbol"] for item in selected], ["A", "B"])

    def test_enforces_per_trade_and_total_premium_limits(self):
        candidates = [
            trade("A", "2026-01-01", "2026-01-03", 1.01, 1.25),
            trade("B", "2026-01-01", "2026-01-03", 0.75, 1.00),
            trade("C", "2026-01-01", "2026-01-03", 0.50, 0.75),
        ]

        selected = apply_portfolio_constraints(
            candidates,
            starting_cash=2500,
            max_positions=2,
            max_total_premium=100,
            max_entry_premium=100,
        )

        self.assertEqual([item["symbol"] for item in selected], ["B"])

    def test_rejects_a_second_correlated_position(self):
        candidates = [
            trade("SPY", "2026-01-01", "2026-01-03", 0.50, 0.75),
            trade("QQQ", "2026-01-01", "2026-01-03", 0.50, 0.75),
        ]

        selected = apply_portfolio_constraints(
            candidates,
            starting_cash=2500,
            max_positions=2,
            max_total_premium=200,
            max_entry_premium=100,
        )

        self.assertEqual([item["symbol"] for item in selected], ["QQQ"])


class SwingSignalTests(unittest.TestCase):
    def test_requires_a_pullback_reclaim_in_a_bullish_regime(self):
        close = pd.Series([101.0, 103.0])
        indicators = {
            "ema_10": pd.Series([102.0, 102.5]),
            "ema_20": pd.Series([102.0, 102.0]),
            "ma_50": pd.Series([100.0, 100.0]),
            "ma_200": pd.Series([95.0, 95.0]),
            "rsi": pd.Series([50.0, 55.0]),
            "macd_hist": pd.Series([0.1, 0.2]),
        }

        self.assertTrue(is_swing_entry_at(close, indicators, len(close) - 1))

    def test_rejects_a_price_below_the_long_term_average(self):
        close = pd.Series(
            list(range(300, 100, -1)) + [102, 103],
            index=pd.date_range("2025-01-01", periods=202),
            dtype=float,
        )
        indicators = build_swing_signals(close)

        self.assertFalse(is_swing_entry_at(close, indicators, len(close) - 1))


class CorrelationGroupTests(unittest.TestCase):
    def test_related_symbols_share_a_group(self):
        self.assertEqual(correlation_group("SPY"), correlation_group("QQQ"))
        self.assertEqual(correlation_group("XOM"), correlation_group("CVX"))

    def test_unlisted_symbol_gets_its_own_group(self):
        self.assertEqual(correlation_group("OTHER"), "OTHER")


if __name__ == "__main__":
    unittest.main()
