"""Completed-bar intraday EMA-cloud signals; no order submission here."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import ta
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.requests import GetCalendarRequest

from bot_logger import bot_log
from config import OASIS_BAR_MINUTES, OASIS_EMA_FAST, OASIS_EMA_SLOW

_history = {}
_last_refresh = None
_session_dates = {}
_calendar_date = None
_MARKET_ZONE = ZoneInfo("America/New_York")


def refresh_oasis_data(symbols, client, calendar_client, now=None):
    """Batch regular-session bars, retaining warm-up history between scans."""
    global _last_refresh, _session_dates, _calendar_date
    now = now or datetime.now(timezone.utc)
    bucket = int(now.timestamp()) // (OASIS_BAR_MINUTES * 60)
    if _last_refresh == bucket:
        return
    try:
        market_date = now.astimezone(_MARKET_ZONE).date()
        if _calendar_date != market_date:
            sessions = calendar_client.get_calendar(GetCalendarRequest(
                start=market_date - timedelta(days=8), end=market_date,
            ))
            _session_dates = {
                session.date: (
                    pd.Timestamp(session.open).tz_localize(_MARKET_ZONE),
                    pd.Timestamp(session.close).tz_localize(_MARKET_ZONE),
                ) for session in sessions
            }
            _calendar_date = market_date
        request = StockBarsRequest(
            symbol_or_symbols=list(symbols),
            timeframe=TimeFrame(OASIS_BAR_MINUTES, TimeFrameUnit.Minute),
            start=now - timedelta(days=8) if any(symbol not in _history for symbol in symbols) else now - timedelta(minutes=20),
            end=now,
            feed=DataFeed.IEX,
        )
        response = client.get_stock_bars(request)
        for symbol in symbols:
            bars = response.data.get(symbol, [])
            if not bars:
                continue
            close = pd.Series(
                [float(bar.close) for bar in bars],
                index=pd.to_datetime([bar.timestamp for bar in bars], utc=True),
                dtype=float,
            )
            local = close.index.tz_convert(_MARKET_ZONE)
            in_session = [
                stamp.date() in _session_dates
                and _session_dates[stamp.date()][0] <= stamp
                and stamp + pd.Timedelta(minutes=OASIS_BAR_MINUTES) <= _session_dates[stamp.date()][1]
                for stamp in local
            ]
            close = close[in_session]
            if close.empty:
                continue
            previous = _history.get(symbol)
            if previous is not None and not previous.empty:
                close = pd.concat([previous, close])
            close = close[~close.index.duplicated(keep="last")].sort_index()
            _history[symbol] = close.tail(600)
        _last_refresh = bucket
    except Exception as exc:
        bot_log(f"Oasis intraday data unavailable: {exc}")


def completed_oasis_close(symbol, now=None):
    now = now or datetime.now(timezone.utc)
    close = _history.get(symbol)
    if close is None or close.empty:
        return None
    close = close[close.index + pd.Timedelta(minutes=OASIS_BAR_MINUTES) <= now]
    if len(close) < 40:
        return None
    latest = close.index[-1]
    if latest.tz_convert(_MARKET_ZONE).date() != now.astimezone(_MARKET_ZONE).date():
        return None
    if now - latest.to_pydatetime() > timedelta(minutes=2 * OASIS_BAR_MINUTES):
        return None
    return close


def oasis_indicators(close):
    macd = ta.trend.MACD(close, window_fast=12, window_slow=26, window_sign=9)
    return {
        "fast": close.ewm(span=OASIS_EMA_FAST, adjust=False).mean(),
        "slow": close.ewm(span=OASIS_EMA_SLOW, adjust=False).mean(),
        "rsi": ta.momentum.RSIIndicator(close, window=14).rsi(),
        "hist": macd.macd_diff(),
    }


def oasis_bullish_at(close, indicators, index):
    if index < 1:
        return False
    fast, slow, rsi, hist = (indicators[key] for key in ("fast", "slow", "rsi", "hist"))
    return bool(
        close.iloc[index] > fast.iloc[index] > slow.iloc[index]
        and fast.iloc[index] > fast.iloc[index - 1]
        and slow.iloc[index] > slow.iloc[index - 1]
        and 50 < rsi.iloc[index] < 70
        and hist.iloc[index] > 0
        and hist.iloc[index] > hist.iloc[index - 1]
        and close.iloc[index] > close.iloc[index - 1]
    )


def get_oasis_signal_state(symbol, now=None):
    close = completed_oasis_close(symbol, now)
    if close is None:
        return {"bullish": False, "new_signal": False, "signal_date": "",
                "data_available": False}
    indicators = oasis_indicators(close)
    current = oasis_bullish_at(close, indicators, len(close) - 1)
    previous = oasis_bullish_at(close, indicators, len(close) - 2)
    return {"bullish": current, "new_signal": current and not previous,
            "signal_date": close.index[-1].isoformat(), "data_available": True}


def oasis_exit_signal(symbol, now=None):
    close = completed_oasis_close(symbol, now)
    if close is None:
        return False, "intraday_data_unavailable"
    indicators = oasis_indicators(close)
    if close.iloc[-1] < indicators["slow"].iloc[-1]:
        return True, "oasis_close_below_ema_cloud"
    if indicators["fast"].iloc[-1] <= indicators["slow"].iloc[-1]:
        return True, "oasis_bearish_ema_cloud"
    if indicators["hist"].iloc[-1] <= 0 or indicators["rsi"].iloc[-1] < 50:
        return True, "oasis_momentum_faded"
    return False, ""
