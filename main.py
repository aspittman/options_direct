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
    OPTION_TYPE,
    CONTRACT_QTY,
    MAX_POSITIONS,
    SCAN_INTERVAL_SECONDS
)

from strategy import is_bullish_setup, wait_for_market_open

from options_trader import (
    trading_client,
    get_open_positions_count,
    already_holding_underlying,
    get_option_contract,
    buy_option_contract
)


def run_bot():
    wait_for_market_open(trading_client)

    print("Starting options paper trading bot...")

    while True:
        for underlying in UNDERLYINGS:
            print(f"\n=== Checking {underlying} ===")

            if get_open_positions_count() >= MAX_POSITIONS:
                print("Max positions reached.")
                break

            if already_holding_underlying(underlying):
                print(f"Already holding option/position related to {underlying}. Skipping.")
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
                print(f"No bullish setup for {underlying}.")
                continue

            option_symbol = get_option_contract(
                underlying,
                option_type=OPTION_TYPE,
                min_dte=MIN_DTE,
                max_dte=MAX_DTE
            )

            if option_symbol:
                buy_option_contract(option_symbol, qty=CONTRACT_QTY)

            time.sleep(2)

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()