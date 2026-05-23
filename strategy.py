import yfinance as yf
import ta


def wait_for_market_open(trading_client):
    import time

    while True:
        clock = trading_client.get_clock()

        if clock.is_open:
            print("Market is open.")
            break

        print("Market closed. Waiting...")
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

        print(f"{symbol}: close={latest_close:.2f}, MA50={latest_ma_short:.2f}, MA200={latest_ma_long:.2f}, MACD hist={latest_macd_hist:.4f}")

        return in_uptrend and ma_rising and macd_confirmed

    except Exception as e:
        print(f"Strategy error for {symbol}: {e}")
        return False