import yfinance as yf
import ta

from bot_logger import bot_log


def wait_for_market_open(trading_client):
    import time

    while True:
        clock = trading_client.get_clock()

        if clock.is_open:
            bot_log("Market is open.")
            break

        bot_log("Market closed. Waiting...")
        time.sleep(60)


def is_bullish_setup(symbol, ma_short, ma_long, macd_fast, macd_slow, macd_signal):
    try:
        data = yf.download(symbol, period="1y", interval="1h", progress=False)

        if data is None or data.empty:
            return False

        close = data["Close"].squeeze()

        if len(close) < ma_long + 5:
            return False

        ma_short_series = close.rolling(ma_short).mean()
        ma_long_series = close.rolling(ma_long).mean()

        macd = ta.trend.MACD(
            close=close,
            window_fast=macd_fast,
            window_slow=macd_slow,
            window_sign=macd_signal
        )

        latest_close = float(close.iloc[-1])
        latest_ma_short = float(ma_short_series.iloc[-1])
        prev_ma_short = float(ma_short_series.iloc[-2])
        latest_ma_long = float(ma_long_series.iloc[-1])

        latest_macd = float(macd.macd().iloc[-1])
        latest_macd_signal = float(macd.macd_signal().iloc[-1])
        latest_macd_hist = float(macd.macd_diff().iloc[-1])

        in_uptrend = latest_close > latest_ma_short > latest_ma_long
        ma_rising = latest_ma_short > prev_ma_short
        macd_confirmed = latest_macd > latest_macd_signal and latest_macd_hist > 0

        bot_log(f"{symbol}: close={latest_close:.2f}, MA50={latest_ma_short:.2f}, MA200={latest_ma_long:.2f}, MACD hist={latest_macd_hist:.4f}")

        return in_uptrend and ma_rising and macd_confirmed

    except Exception as e:
        bot_log(f"Strategy error for {symbol}: {e}")
        return False


def is_market_regime_bullish(symbol, short_ma, long_ma):
    try:
        data = yf.download(symbol, period="2y", interval="1d", progress=False)

        if data is None or data.empty:
            return False

        close = data["Close"].squeeze()

        if len(close) < long_ma + 5:
            return False

        short_series = close.rolling(short_ma).mean()
        long_series = close.rolling(long_ma).mean()

        latest_close = float(close.iloc[-1])
        latest_short = float(short_series.iloc[-1])
        latest_long = float(long_series.iloc[-1])

        bullish = latest_close > latest_long and latest_short > latest_long
        bot_log(
            f"Market regime {symbol}: close={latest_close:.2f}, "
            f"MA{short_ma}={latest_short:.2f}, MA{long_ma}={latest_long:.2f}, "
            f"bullish={bullish}"
        )

        return bullish

    except Exception as e:
        bot_log(f"Market regime error for {symbol}: {e}")
        return False


def is_underlying_exit_signal(symbol, ma_short, ma_long, macd_fast, macd_slow, macd_signal):
    try:
        data = yf.download(symbol, period="1y", interval="1h", progress=False)

        if data is None or data.empty:
            return False, "no_data"

        close = data["Close"].squeeze()

        if len(close) < ma_long + 5:
            return False, "not_enough_data"

        ma_short_series = close.rolling(ma_short).mean()
        ma_long_series = close.rolling(ma_long).mean()

        macd = ta.trend.MACD(
            close=close,
            window_fast=macd_fast,
            window_slow=macd_slow,
            window_sign=macd_signal
        )

        latest_close = float(close.iloc[-1])
        latest_ma_short = float(ma_short_series.iloc[-1])
        latest_ma_long = float(ma_long_series.iloc[-1])
        latest_macd_hist = float(macd.macd_diff().iloc[-1])

        if latest_close < latest_ma_long:
            return True, "close_below_long_ma"

        if latest_ma_short < latest_ma_long:
            return True, "short_ma_below_long_ma"

        if latest_macd_hist < 0:
            return True, "macd_hist_negative"

        return False, ""

    except Exception as e:
        bot_log(f"Exit signal error for {symbol}: {e}")
        return False, "exit_signal_error"
