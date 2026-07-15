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
    UNDERLYING_STOP_LOSS_PCT,
    UNDERLYING_TAKE_PROFIT_PCT,
    SCAN_INTERVAL_SECONDS
)

from analytics import record_event
from bot_logger import bot_log, setup_logging
from strategy import (
    is_bullish_setup,
    is_market_regime_bullish,
    is_underlying_exit_signal,
    wait_for_market_open
)

from options_trader import (
    trading_client,
    get_open_positions_count,
    already_holding_underlying,
    has_earnings_soon,
    get_option_contract,
    manage_underlying_exits,
    buy_option_contract,
    log_open_option_positions,
    log_analytics_summary,
)


def run_bot():
    setup_logging()
    wait_for_market_open(trading_client)

    bot_log("Starting options paper trading bot...")

    while True:
        log_open_option_positions()
        log_analytics_summary()
        manage_underlying_exits(
            UNDERLYINGS,
            lambda symbol: is_underlying_exit_signal(
                symbol,
                MA_SHORT,
                MA_LONG,
                MACD_FAST,
                MACD_SLOW,
                MACD_SIGNAL
            ),
            UNDERLYING_STOP_LOSS_PCT,
            UNDERLYING_TAKE_PROFIT_PCT
        )

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

            if get_open_positions_count() >= MAX_POSITIONS:
                open_positions = log_open_option_positions()
                position_symbols = [position.symbol for position in open_positions]
                bot_log(
                    f"Position limit: MAX_POSITIONS={MAX_POSITIONS}, "
                    f"open_option_positions={len(open_positions)}, symbols={position_symbols}"
                )
                bot_log("Max positions reached.")
                record_event(
                    "SKIP", underlying=underlying, reason="max_positions",
                    details=(f"MAX_POSITIONS={MAX_POSITIONS};count={len(open_positions)};"
                             f"symbols={','.join(position_symbols)}")
                )
                break

            if already_holding_underlying(underlying):
                bot_log(f"Already holding option/position related to {underlying}. Skipping.")
                record_event("SKIP", underlying=underlying, reason="already_holding")
                continue

            if has_earnings_soon(underlying):
                continue

            bullish = is_bullish_setup(
                underlying,
                MA_SHORT,
                MA_LONG,
                MACD_FAST,
                MACD_SLOW,
                MACD_SIGNAL
            )

            if not bullish:
                bot_log(f"No bullish setup for {underlying}.")
                record_event("SKIP", underlying=underlying, reason="not_bullish")
                continue

            option_symbol = get_option_contract(
                underlying,
                option_type=OPTION_TYPE,
                min_dte=MIN_DTE,
                max_dte=MAX_DTE
            )

            if option_symbol:
                buy_option_contract(option_symbol, qty=CONTRACT_QTY, underlying=underlying)

            time.sleep(2)

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
