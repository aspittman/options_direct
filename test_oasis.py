import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from contextlib import ExitStack

import main

import pandas as pd

import analytics
import oasis
import options_trader
from config import PAPER_STRATEGIES


def fill(strategy, side, price, day, qty=1, symbol="ABC261218C00100000"):
    return dict(event="ORDER_FILL", strategy=strategy, underlying="ABC",
                option_symbol=symbol, qty=str(qty), price=str(price),
                order_side=side, timestamp=f"{day}T10:00:00")


class SharedLossBlockTests(unittest.TestCase):
    def test_loss_in_either_strategy_blocks_same_underlying_through_day_30(self):
        for name in ("regular", "oasis", "max_100"):
            rows = [fill(name, "buy", 4, "2026-08-10"),
                    fill(name, "sell", 3, "2026-08-11")]
            losses = analytics.latest_underlying_loss_dates(rows)
            for day in (date(2026, 8, 11), date(2026, 9, 10)):
                self.assertTrue(analytics.loss_reentry_block_active("ABC", day, losses))
            self.assertFalse(analytics.loss_reentry_block_active("ABC", date(2026, 9, 11), losses))
            self.assertFalse(analytics.loss_reentry_block_active("XYZ", date(2026, 8, 11), losses))

    def test_fifo_partial_loss_and_profit_do_not_clear_block(self):
        rows = [fill("regular", "buy", 4, "2026-08-10", qty=2),
                fill("regular", "sell", 3, "2026-08-11"),
                fill("regular", "sell", 5, "2026-08-12"),
                fill("oasis", "buy", 2, "2026-09-01"),
                fill("oasis", "sell", 1, "2026-09-02")]
        self.assertEqual(analytics.latest_underlying_loss_dates(rows[:3]), {"ABC": date(2026, 8, 11)})
        self.assertEqual(analytics.latest_underlying_loss_dates(rows), {"ABC": date(2026, 9, 2)})

    def test_winning_breakeven_unfilled_and_unknown_basis_do_not_create_loss(self):
        rows = [fill("regular", "buy", 4, "2026-08-10"),
                fill("regular", "sell", 4, "2026-08-11"),
                fill("regular", "buy", 4, "2026-08-12"),
                fill("regular", "sell", 5, "2026-08-13"),
                fill("oasis", "sell", 1, "2026-08-14"),
                dict(event="ORDER_FAILED", strategy="regular")]
        self.assertEqual(analytics.latest_underlying_loss_dates(rows), {})

    def test_submission_guard_blocks_both_variants_before_broker_access(self):
        with patch.object(options_trader, "loss_reentry_block_active", return_value=True), \
             patch.object(options_trader, "get_options_direct_positions") as broker, \
             patch.object(options_trader, "record_event"):
            for variant in PAPER_STRATEGIES:
                self.assertFalse(options_trader.buy_option_contract(
                    "ABC261218C00100000", underlying="ABC", strategy=variant["name"]))
            broker.assert_not_called()


class OasisSignalTests(unittest.TestCase):
    def test_incomplete_stale_and_previous_session_bars_are_excluded(self):
        now = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)
        close = pd.Series(range(55), index=pd.date_range("2026-09-14 13:30", periods=55, freq="5min", tz="UTC"))
        with patch.object(oasis, "_history", {"ABC": close}):
            completed = oasis.completed_oasis_close("ABC", now)
            self.assertEqual(completed.index[-1], pd.Timestamp("2026-09-14 17:55Z"))
            self.assertIsNone(oasis.completed_oasis_close("ABC", now + timedelta(minutes=20)))
            self.assertIsNone(oasis.completed_oasis_close("ABC", now + timedelta(days=1)))

    def test_batch_refresh_excludes_premarket_and_early_close_afterhours(self):
        client, calendar = MagicMock(), MagicMock()
        calendar.get_calendar.return_value = [SimpleNamespace(
            date=date(2026, 11, 27), open=datetime(2026, 11, 27, 9, 30),
            close=datetime(2026, 11, 27, 13, 0))]
        bars = [SimpleNamespace(timestamp=pd.Timestamp(stamp), close=100)
                for stamp in ("2026-11-27 14:25Z", "2026-11-27 14:30Z",
                              "2026-11-27 17:55Z", "2026-11-27 18:00Z")]
        client.get_stock_bars.return_value = SimpleNamespace(data={"ABC": bars, "XYZ": bars})
        now = datetime(2026, 11, 27, 18, 1, tzinfo=timezone.utc)
        with patch.object(oasis, "_history", {}), patch.object(oasis, "_last_refresh", None), \
             patch.object(oasis, "_calendar_date", None), patch.object(oasis, "_session_dates", {}):
            oasis.refresh_oasis_data(["ABC", "XYZ"], client, calendar, now)
            oasis.refresh_oasis_data(["ABC", "XYZ"], client, calendar, now)
            self.assertEqual(len(oasis._history["ABC"]), 2)
            self.assertEqual(oasis._history["ABC"].index[-1], pd.Timestamp("2026-11-27 17:55Z"))
            client.get_stock_bars.assert_called_once()
            self.assertEqual(client.get_stock_bars.call_args.args[0].symbol_or_symbols, ["ABC", "XYZ"])

    def test_bullish_cloud_requires_rising_momentum_and_not_overbought(self):
        close = pd.Series([100, 101, 103])
        indicators = dict(fast=pd.Series([99, 100, 102]), slow=pd.Series([98, 99, 100]),
                          rsi=pd.Series([55, 56, 60]), hist=pd.Series([0.1, 0.2, 0.3]))
        self.assertTrue(oasis.oasis_bullish_at(close, indicators, 2))
        indicators["hist"].iloc[-1] = 0.15
        self.assertFalse(oasis.oasis_bullish_at(close, indicators, 2))
        indicators["hist"].iloc[-1] = 0.3
        indicators["rsi"].iloc[-1] = 75
        self.assertFalse(oasis.oasis_bullish_at(close, indicators, 2))

    def test_fresh_transition_only_and_durable_intraday_signal_identifier(self):
        close = pd.Series(range(40), index=pd.date_range("2026-09-14 13:30", periods=40, freq="5min", tz="UTC"))
        with patch.object(oasis, "completed_oasis_close", return_value=close), \
             patch.object(oasis, "oasis_indicators", return_value={}), \
             patch.object(oasis, "oasis_bullish_at", side_effect=[True, False, True, True]):
            fresh = oasis.get_oasis_signal_state("ABC")
            ongoing = oasis.get_oasis_signal_state("ABC")
        self.assertTrue(fresh["new_signal"])
        self.assertFalse(ongoing["new_signal"])
        self.assertEqual(fresh["signal_date"], close.index[-1].isoformat())

    def test_cloud_break_and_momentum_exit(self):
        close = pd.Series([100, 99])
        with patch.object(oasis, "completed_oasis_close", return_value=close), \
             patch.object(oasis, "oasis_indicators", return_value={
                 "fast": pd.Series([101, 101]), "slow": pd.Series([100, 100]),
                 "hist": pd.Series([0.2, 0.3]), "rsi": pd.Series([55, 56])}):
            self.assertEqual(oasis.oasis_exit_signal("ABC"), (True, "oasis_close_below_ema_cloud"))


class OasisExecutionTests(unittest.TestCase):
    def run_exit(self, name="oasis", price=4, minutes_left=120, opened_at="2026-09-14T10:00:00"):
        symbol = "ABC261218C00100000"
        now = datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)
        clock = SimpleNamespace(timestamp=now, next_close=now + timedelta(minutes=minutes_left), is_open=True)
        lot = dict(qty=1, cost=4, underlying_cost=100, opened_at=opened_at)
        with patch.object(options_trader, "get_options_direct_positions", return_value=[SimpleNamespace(symbol=symbol, current_price=price)]), \
             patch.object(options_trader, "get_strategy_open_lots", return_value={(name, "ABC", symbol): lot}), \
             patch.object(options_trader, "get_underlying_high_water_marks", return_value={}), \
             patch.object(options_trader, "get_underlying_price", return_value=100), \
             patch.object(options_trader.trading_client, "get_clock", return_value=clock), \
             patch.object(options_trader, "record_event"), \
             patch.object(options_trader, "close_strategy_lot") as close:
            options_trader.manage_underlying_exits(["ABC"], lambda *args: (False, ""), .03, .08)
            return close.call_args

    def test_oasis_twenty_percent_stop_and_regular_original_thirty_percent_stop(self):
        self.assertIn("option_stop_loss", self.run_exit(price=3.2).args[-1])
        self.assertIsNone(self.run_exit(price=3.21))
        self.assertIsNone(self.run_exit(name="regular", price=3.2))
        self.assertIn("option_stop_loss", self.run_exit(name="regular", price=2.8).args[-1])

    def test_early_close_and_overnight_recovery_only_close_oasis(self):
        self.assertEqual(self.run_exit(minutes_left=15).args[-1], "oasis_session_close")
        self.assertIsNone(self.run_exit(name="regular", minutes_left=15))
        self.assertEqual(self.run_exit(opened_at="2026-09-11T10:00:00").args[-1], "oasis_overnight_recovery")

    def test_cutoff_blocks_order_submission_at_broker_reported_early_close(self):
        now = datetime(2026, 11, 27, 17, 30, tzinfo=timezone.utc)
        clock = SimpleNamespace(timestamp=now, next_close=now + timedelta(minutes=30), is_open=True)
        with patch.object(options_trader, "loss_reentry_block_active", return_value=False), \
             patch.object(options_trader.trading_client, "get_clock", return_value=clock), \
             patch.object(options_trader, "get_options_direct_positions") as broker, \
             patch.object(options_trader, "record_event"):
            self.assertFalse(options_trader.buy_option_contract("ABC261218C00100000", strategy="oasis"))
            broker.assert_not_called()

    def test_cancel_request_remains_pending_until_fill_confirmed(self):
        rows = [dict(event="ORDER_SUBMITTED", order_id="123", order_side="buy"),
                dict(event="ORDER_CANCEL_REQUESTED", order_id="123")]
        with patch.object(analytics, "read_events", return_value=rows):
            self.assertIn("123", analytics.get_submitted_orders())
            rows.append(dict(event="ORDER_FILL", order_id="123"))
            self.assertNotIn("123", analytics.get_submitted_orders())

    def test_fill_arriving_after_cancel_request_is_still_recorded(self):
        rows = [dict(event="ORDER_SUBMITTED", strategy="oasis", underlying="ABC",
                     option_symbol="ABC261218C00100000", order_id="123", order_side="buy",
                     timestamp="2020-01-01T10:00:00", underlying_price="100")]
        new = SimpleNamespace(status="new", filled_qty="0", filled_avg_price=None)
        filled = SimpleNamespace(status="filled", filled_qty="1", filled_avg_price="4")
        def record(event, **kwargs):
            rows.append(dict(event=event, **kwargs))
        with patch.object(analytics, "read_events", return_value=rows), \
             patch.object(options_trader, "record_event", side_effect=record), \
             patch.object(options_trader, "is_own_order", return_value=True), \
             patch.object(options_trader, "trading_client") as broker:
            broker.get_order_by_id.side_effect = [new, filled]
            options_trader.reconcile_order_fills()
            self.assertIn("123", analytics.get_submitted_orders())
            options_trader.reconcile_order_fills()
            self.assertNotIn("123", analytics.get_submitted_orders())
            self.assertEqual([row["event"] for row in rows],
                             ["ORDER_SUBMITTED", "ORDER_CANCEL_REQUESTED", "ORDER_FILL"])

    def test_loss_cancels_pending_buys_for_both_strategies(self):
        now = datetime.now(timezone.utc)
        clock = SimpleNamespace(timestamp=now, next_close=now + timedelta(hours=3), is_open=True)
        orders = {str(i): dict(strategy=name, underlying="ABC", order_side="buy")
                  for i, name in enumerate(("regular", "oasis"))}
        with patch.object(options_trader, "latest_underlying_loss_dates", return_value={"ABC": now.date()}), \
             patch.object(options_trader, "get_submitted_orders", return_value=orders), \
             patch.object(options_trader, "is_own_order", return_value=True), \
             patch.object(options_trader, "trading_client") as broker, \
             patch.object(options_trader, "record_event") as record:
            options_trader.cancel_blocked_entry_orders(clock)
            self.assertEqual(broker.cancel_order_by_id.call_count, 2)
            self.assertTrue(all(call.args[0] == "ORDER_CANCEL_REQUESTED" for call in record.call_args_list))


class RuntimeSchedulingTests(unittest.TestCase):
    def test_regular_daily_gate_does_not_suppress_oasis_on_next_cycle(self):
        now = datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)
        clock = SimpleNamespace(timestamp=now, next_close=now + timedelta(hours=4), is_open=True)
        no_signal = dict(bullish=False, new_signal=False, data_available=True, signal_date="")
        with ExitStack() as stack:
            for name in ("setup_logging", "configure_daily_data_client", "wait_for_market_open",
                         "reconcile_order_fills", "cancel_blocked_entry_orders", "refresh_oasis_data",
                         "bootstrap_legacy_positions", "reconcile_strategy_lots_with_broker",
                         "log_open_option_positions", "log_account_info", "log_analytics_summary",
                         "manage_underlying_exits", "record_event", "bot_log"):
                stack.enter_context(patch.object(main, name))
            stack.enter_context(patch.object(main, "UNDERLYINGS", ["ABC"]))
            stack.enter_context(patch.object(main, "ENABLE_NEW_ENTRIES", True))
            stack.enter_context(patch.object(main.trading_client, "get_clock", return_value=clock))
            stack.enter_context(patch.object(main, "latest_completed_bar_date", return_value=date(2026, 9, 11)))
            stack.enter_context(patch.object(main, "latest_underlying_loss_dates", return_value={}))
            stack.enter_context(patch.object(main, "get_submitted_orders", return_value={}))
            stack.enter_context(patch.object(main, "is_market_regime_bullish", return_value=True))
            stack.enter_context(patch.object(main, "has_earnings_soon", return_value=False))
            daily = stack.enter_context(patch.object(main, "get_bullish_signal_state", return_value=no_signal))
            intraday = stack.enter_context(patch.object(main, "get_oasis_signal_state", return_value=no_signal))
            stack.enter_context(patch.object(main.time, "sleep", side_effect=[None, StopIteration]))
            with self.assertRaises(StopIteration):
                main.run_bot()
            self.assertEqual(daily.call_count, 1)
            self.assertEqual(intraday.call_count, 2)


if __name__ == "__main__":
    unittest.main()
