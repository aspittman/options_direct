import time

from config import (
    UNDERLYINGS,
    MA_SHORT,
    MA_LONG,
    MACD_FAST,
    MACD_SLOW,
    MACD_SIGNAL,
    MIN_DTE,
    MAX_DTE,
    ENABLE_MARKET_REGIME_FILTER,
    MARKET_REGIME_SYMBOL,
    MARKET_REGIME_SHORT_MA,
    MARKET_REGIME_LONG_MA,
    OPTION_TYPE,
    CONTRACT_QTY,
    MAX_POSITIONS,
    MAX_POSITIONS_PER_CORRELATION_GROUP,
    correlation_group,
    ENABLE_NEW_ENTRIES,
    PAPER_STRATEGIES,
    UNDERLYING_STOP_LOSS_PCT,
    UNDERLYING_TAKE_PROFIT_PCT,
    SCAN_INTERVAL_SECONDS
)

from analytics import get_strategy_open_lots, get_submitted_orders, record_event
from bot_logger import bot_log, setup_logging
from strategy import (
    is_bullish_setup,
    is_market_regime_bullish,
    is_underlying_exit_signal,
    wait_for_market_open
)

from options_trader import (
    trading_client,
    has_earnings_soon,
    get_option_contract,
    manage_underlying_exits,
    buy_option_contract,
    log_open_option_positions,
    log_analytics_summary,
    reconcile_order_fills,
    bootstrap_legacy_positions,
)


def run_bot():
    setup_logging()
    wait_for_market_open(trading_client)

    bot_log("Starting options paper trading bot...")
    if not ENABLE_NEW_ENTRIES:
        bot_log("New entries are disabled; existing positions will still be managed.")

    while True:
        reconcile_order_fills()
        bootstrap_legacy_positions()
        log_open_option_positions()
        log_analytics_summary()
        manage_underlying_exits(
            UNDERLYINGS,
            lambda symbol, signal: is_underlying_exit_signal(
                symbol,
                MA_SHORT,
                MA_LONG,
                MACD_FAST,
                MACD_SLOW,
                MACD_SIGNAL,
                signal=signal,
            ),
            UNDERLYING_STOP_LOSS_PCT,
            UNDERLYING_TAKE_PROFIT_PCT
        )

        if not ENABLE_NEW_ENTRIES:
            record_event("SKIP", reason="new_entries_disabled")
            time.sleep(SCAN_INTERVAL_SECONDS)
            continue

        market_regime_ok = True
        if ENABLE_MARKET_REGIME_FILTER:
            market_regime_ok = is_market_regime_bullish(
                MARKET_REGIME_SYMBOL,
                MARKET_REGIME_SHORT_MA,
                MARKET_REGIME_LONG_MA
            )

            if not market_regime_ok:
                bot_log("Market regime is not bullish. Skipping new entries this cycle.")
                record_event(
                    "SKIP",
                    underlying=MARKET_REGIME_SYMBOL,
                    reason="market_regime_not_bullish"
                )

        for underlying in UNDERLYINGS:
            bot_log(f"=== Checking {underlying} ===")

            if not market_regime_ok:
                continue

            if has_earnings_soon(underlying):
                continue

            eligible_variants = []
            for variant in PAPER_STRATEGIES:
                bullish = is_bullish_setup(
                    underlying,
                    MA_SHORT,
                    MA_LONG,
                    MACD_FAST,
                    MACD_SLOW,
                    MACD_SIGNAL,
                    signal=variant["signal"],
                )
                if bullish:
                    eligible_variants.append(variant)
                else:
                    record_event(
                        "SKIP", strategy=variant["name"], underlying=underlying,
                        reason=f"not_bullish_{variant['signal']}"
                    )

            if not eligible_variants:
                bot_log(f"No daily bullish setup for {underlying}.")
                continue

            option_symbol = get_option_contract(
                underlying,
                option_type=OPTION_TYPE,
                min_dte=MIN_DTE,
                max_dte=MAX_DTE
            )

            if option_symbol:
                lots = get_strategy_open_lots()
                pending = get_submitted_orders().values()
                reserved_count = len(lots) + sum(
                    1 for row in pending if row.get("order_side") == "buy"
                )
                reserved_group_counts = {}
                for _, lot_underlying, _ in lots:
                    group = correlation_group(lot_underlying)
                    reserved_group_counts[group] = reserved_group_counts.get(group, 0) + 1
                for row in pending:
                    if row.get("order_side") == "buy":
                        group = correlation_group(row.get("underlying", ""))
                        reserved_group_counts[group] = reserved_group_counts.get(group, 0) + 1
                for variant in eligible_variants:
                    strategy_name = variant["name"]
                    if reserved_count >= MAX_POSITIONS:
                        bot_log(
                            f"Global position limit reached: strategy={strategy_name} "
                            f"MAX_POSITIONS={MAX_POSITIONS}"
                        )
                        record_event(
                            "SKIP", strategy=strategy_name, underlying=underlying,
                            reason="max_positions"
                        )
                        continue
                    target_group = correlation_group(underlying)
                    if reserved_group_counts.get(target_group, 0) >= MAX_POSITIONS_PER_CORRELATION_GROUP:
                        bot_log(
                            f"Correlation-group limit reached: group={target_group} "
                            f"underlying={underlying}"
                        )
                        record_event(
                            "SKIP", strategy=strategy_name, underlying=underlying,
                            reason=f"correlation_group_{target_group}"
                        )
                        continue
                    already_holds = any(
                        strategy == strategy_name and lot_underlying == underlying
                        for strategy, lot_underlying, _ in lots
                    )
                    if already_holds:
                        record_event(
                            "SKIP", strategy=strategy_name, underlying=underlying,
                            reason="already_holding"
                        )
                        continue
                    submitted = buy_option_contract(
                        option_symbol,
                        qty=CONTRACT_QTY,
                        underlying=underlying,
                        strategy=strategy_name,
                        max_entry_premium=variant["max_premium"],
                    )
                    if submitted:
                        reserved_count += 1
                        reserved_group_counts[target_group] = (
                            reserved_group_counts.get(target_group, 0) + 1
                        )

            time.sleep(2)

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
