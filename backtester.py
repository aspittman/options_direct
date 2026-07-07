import argparse
import csv
from pathlib import Path

import ta
import yfinance as yf

from config import (
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    MA_LONG,
    MA_SHORT,
    MAX_HOLDING_DAYS,
    OPTION_LEVERAGE_MULTIPLIER,
    OPTION_PREMIUM_ESTIMATE_PERCENT,
    OPTION_STOP_LOSS_PERCENT,
    OPTION_TAKE_PROFIT_PERCENT,
    UNDERLYINGS,
)


RESULTS_FILE = Path("logs/options_backtest_trades.csv")
CONTRACT_MULTIPLIER = 100

FIELDNAMES = [
    "symbol",
    "entry_date",
    "exit_date",
    "entry_underlying_price",
    "exit_underlying_price",
    "estimated_option_entry_price",
    "estimated_option_exit_price",
    "pnl_dollars",
    "pnl_percent",
    "exit_reason",
]


def get_close_series(symbol, period, interval):
    data = yf.download(symbol, period=period, interval=interval, progress=False)

    if data is None or data.empty:
        return None

    close = data["Close"]
    if hasattr(close, "columns"):
        close = close.squeeze()

    close = close.dropna()
    return close if not close.empty else None


def build_signals(close):
    ma_short_series = close.rolling(MA_SHORT).mean()
    ma_long_series = close.rolling(MA_LONG).mean()

    macd = ta.trend.MACD(
        close=close,
        window_fast=MACD_FAST,
        window_slow=MACD_SLOW,
        window_sign=MACD_SIGNAL,
    )

    indicators = {
        "ma_short": ma_short_series,
        "ma_long": ma_long_series,
        "macd": macd.macd(),
        "macd_signal": macd.macd_signal(),
        "macd_hist": macd.macd_diff(),
    }
    return indicators


def is_bullish_at(close, indicators, index):
    if index <= 0:
        return False

    latest_close = close.iloc[index]
    latest_ma_short = indicators["ma_short"].iloc[index]
    prev_ma_short = indicators["ma_short"].iloc[index - 1]
    latest_ma_long = indicators["ma_long"].iloc[index]
    latest_macd = indicators["macd"].iloc[index]
    latest_macd_signal = indicators["macd_signal"].iloc[index]
    latest_macd_hist = indicators["macd_hist"].iloc[index]

    values = [
        latest_close,
        latest_ma_short,
        prev_ma_short,
        latest_ma_long,
        latest_macd,
        latest_macd_signal,
        latest_macd_hist,
    ]
    if any(value != value for value in values):
        return False

    in_uptrend = latest_close > latest_ma_short > latest_ma_long
    ma_rising = latest_ma_short > prev_ma_short
    macd_confirmed = latest_macd > latest_macd_signal and latest_macd_hist > 0

    return in_uptrend and ma_rising and macd_confirmed


def bearish_exit_reason(close, indicators, index):
    latest_close = close.iloc[index]
    latest_ma_short = indicators["ma_short"].iloc[index]
    latest_ma_long = indicators["ma_long"].iloc[index]
    latest_macd_hist = indicators["macd_hist"].iloc[index]

    values = [latest_close, latest_ma_short, latest_ma_long, latest_macd_hist]
    if any(value != value for value in values):
        return ""

    if latest_close < latest_ma_long:
        return "close_below_long_ma"

    if latest_ma_short < latest_ma_long:
        return "short_ma_below_long_ma"

    if latest_macd_hist < 0:
        return "macd_hist_negative"

    return ""


def estimate_option_exit_price(entry_option_price, entry_underlying_price, exit_underlying_price):
    underlying_change_pct = (exit_underlying_price - entry_underlying_price) / entry_underlying_price
    option_pnl_pct = underlying_change_pct * OPTION_LEVERAGE_MULTIPLIER
    option_exit_price = entry_option_price * (1 + option_pnl_pct)

    return max(option_exit_price, 0), option_pnl_pct


def build_trade(symbol, close, entry_index, exit_index, exit_reason):
    entry_underlying_price = float(close.iloc[entry_index])
    exit_underlying_price = float(close.iloc[exit_index])
    option_entry_price = entry_underlying_price * OPTION_PREMIUM_ESTIMATE_PERCENT
    option_exit_price, pnl_percent = estimate_option_exit_price(
        option_entry_price,
        entry_underlying_price,
        exit_underlying_price,
    )
    pnl_dollars = (option_exit_price - option_entry_price) * CONTRACT_MULTIPLIER

    return {
        "symbol": symbol,
        "entry_date": close.index[entry_index].date().isoformat(),
        "exit_date": close.index[exit_index].date().isoformat(),
        "entry_underlying_price": round(entry_underlying_price, 2),
        "exit_underlying_price": round(exit_underlying_price, 2),
        "estimated_option_entry_price": round(option_entry_price, 2),
        "estimated_option_exit_price": round(option_exit_price, 2),
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_percent": round(pnl_percent * 100, 2),
        "exit_reason": exit_reason,
    }


def backtest_symbol(symbol, period, interval):
    close = get_close_series(symbol, period, interval)
    if close is None:
        print(f"{symbol}: no historical data")
        return []

    minimum_bars = max(MA_LONG, MACD_SLOW + MACD_SIGNAL) + 5
    if len(close) < minimum_bars:
        print(f"{symbol}: not enough historical data ({len(close)} bars)")
        return []

    indicators = build_signals(close)
    trades = []
    entry_index = None

    for index in range(minimum_bars, len(close)):
        if entry_index is None:
            if is_bullish_at(close, indicators, index):
                entry_index = index
            continue

        entry_underlying_price = float(close.iloc[entry_index])
        exit_underlying_price = float(close.iloc[index])
        option_entry_price = entry_underlying_price * OPTION_PREMIUM_ESTIMATE_PERCENT
        _, option_pnl_pct = estimate_option_exit_price(
            option_entry_price,
            entry_underlying_price,
            exit_underlying_price,
        )

        exit_reason = ""
        if option_pnl_pct <= -OPTION_STOP_LOSS_PERCENT:
            exit_reason = "option_stop_loss"
        elif option_pnl_pct >= OPTION_TAKE_PROFIT_PERCENT:
            exit_reason = "option_take_profit"
        elif index - entry_index >= MAX_HOLDING_DAYS:
            exit_reason = "max_holding_days"
        else:
            exit_reason = bearish_exit_reason(close, indicators, index)

        if exit_reason:
            trades.append(build_trade(symbol, close, entry_index, index, exit_reason))
            entry_index = None

    if entry_index is not None:
        trades.append(build_trade(symbol, close, entry_index, len(close) - 1, "end_of_backtest"))

    return trades


def save_trades(trades):
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_FILE.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(trades)


def print_summary(trades):
    print("\nOptions Backtest Summary")
    print("========================")

    total_trades = len(trades)
    print(f"Total trades: {total_trades}")

    if not trades:
        print("Win rate: 0.00%")
        print("Total P/L: $0.00")
        print("Average win: $0.00")
        print("Average loss: $0.00")
        print("Profit factor: 0.00")
        print("Best symbol: n/a")
        print("Worst symbol: n/a")
        print("Trades by symbol: n/a")
        return

    pnl_values = [float(trade["pnl_dollars"]) for trade in trades]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    win_rate = len(wins) / total_trades
    total_pnl = sum(pnl_values)
    average_win = sum(wins) / len(wins) if wins else 0
    average_loss = sum(losses) / len(losses) if losses else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    symbol_pnl = {}
    symbol_counts = {}
    for trade in trades:
        symbol = trade["symbol"]
        symbol_pnl[symbol] = symbol_pnl.get(symbol, 0) + float(trade["pnl_dollars"])
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

    best_symbol = max(symbol_pnl, key=symbol_pnl.get)
    worst_symbol = min(symbol_pnl, key=symbol_pnl.get)
    profit_factor_text = "inf" if profit_factor == float("inf") else f"{profit_factor:.2f}"

    print(f"Win rate: {win_rate:.2%}")
    print(f"Total P/L: ${total_pnl:.2f}")
    print(f"Average win: ${average_win:.2f}")
    print(f"Average loss: ${average_loss:.2f}")
    print(f"Profit factor: {profit_factor_text}")
    print(f"Best symbol: {best_symbol} (${symbol_pnl[best_symbol]:.2f})")
    print(f"Worst symbol: {worst_symbol} (${symbol_pnl[worst_symbol]:.2f})")
    print("Trades by symbol:")
    for symbol in sorted(symbol_counts):
        print(f"  {symbol}: {symbol_counts[symbol]} trades, ${symbol_pnl[symbol]:.2f} P/L")


def run_backtest(period, interval):
    all_trades = []

    for symbol in UNDERLYINGS:
        print(f"Backtesting {symbol}...")
        all_trades.extend(backtest_symbol(symbol, period, interval))

    save_trades(all_trades)
    print_summary(all_trades)
    print(f"\nSaved trades to {RESULTS_FILE}")


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest the long call options strategy.")
    parser.add_argument("--period", default="1y", help="yfinance period to backtest. Default: 1y")
    parser.add_argument("--interval", default="1d", help="yfinance interval to backtest. Default: 1d")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_backtest(args.period, args.interval)
