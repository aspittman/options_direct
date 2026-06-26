from datetime import date, datetime, timedelta

import yfinance as yf
from alpaca.data.enums import OptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import (
    OptionBarsRequest,
    OptionSnapshotRequest,
    StockLatestTradeRequest
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    ClosePositionRequest,
    MarketOrderRequest,
    GetOptionContractsRequest
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    ContractType,
    AssetStatus
)
from alpaca.common.exceptions import APIError
from requests.exceptions import RequestException

from config import (
    API_KEY,
    SECRET_KEY,
    ALPACA_PAPER,
    DELTA_TOLERANCE,
    EARNINGS_SKIP_DAYS,
    MAX_BID_ASK_SPREAD_PCT,
    MIN_OPEN_INTEREST,
    MIN_OPTION_VOLUME,
    OPTION_DATA_FEED,
    TARGET_DELTA
)
from analytics import get_latest_entry_price, record_event
from bot_logger import bot_log

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=ALPACA_PAPER)
option_data_client = OptionHistoricalDataClient(API_KEY, SECRET_KEY)
stock_data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)


def _option_feed():
    feed = (OPTION_DATA_FEED or "").lower()
    if feed == "opra":
        return OptionsFeed.OPRA
    if feed == "indicative":
        return OptionsFeed.INDICATIVE
    return None


def _to_float(value):
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_open_positions_count():
    try:
        return len(trading_client.get_all_positions())
    except Exception:
        return 0


def has_open_order(symbol):
    try:
        orders = trading_client.get_orders()
        return any(order.symbol == symbol for order in orders)
    except Exception:
        return False


def already_holding_underlying(underlying):
    try:
        positions = trading_client.get_all_positions()

        for position in positions:
            if position.symbol.startswith(underlying):
                return True

        return False

    except Exception:
        return False


def has_earnings_soon(underlying, skip_days=EARNINGS_SKIP_DAYS):
    today = date.today()
    last_skip_date = today + timedelta(days=skip_days)

    try:
        earnings = yf.Ticker(underlying).get_earnings_dates(limit=12)

        if earnings is None or earnings.empty:
            return False

        for earnings_date in earnings.index:
            if isinstance(earnings_date, datetime):
                earnings_day = earnings_date.date()
            else:
                earnings_day = earnings_date

            if today <= earnings_day <= last_skip_date:
                bot_log(f"{underlying}: earnings on {earnings_day}. Skipping.")
                record_event(
                    "SKIP",
                    underlying=underlying,
                    reason="earnings_soon",
                    details=f"earnings_date={earnings_day}"
                )
                return True

        return False

    except Exception as e:
        bot_log(f"Could not check earnings for {underlying}: {e}")
        return False


def get_underlying_price(underlying):
    try:
        request = StockLatestTradeRequest(symbol_or_symbols=underlying)
        trade = stock_data_client.get_stock_latest_trade(request)

        if isinstance(trade, dict):
            trade = trade.get(underlying)

        price = _to_float(getattr(trade, "price", None))
        if price and price > 0:
            return price

    except Exception as e:
        bot_log(f"Could not get Alpaca latest trade for {underlying}: {e}")

    try:
        data = yf.download(underlying, period="5d", interval="1d", progress=False)
        if data is None or data.empty:
            return None

        return float(data["Close"].squeeze().iloc[-1])

    except Exception as e:
        bot_log(f"Could not get Yahoo price for {underlying}: {e}")
        return None


def get_option_volumes(symbols):
    if not symbols:
        return {}

    try:
        request = OptionBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.combine(date.today(), datetime.min.time()),
            feed=_option_feed()
        )
        bars = option_data_client.get_option_bars(request)
        volumes = {}

        for symbol in symbols:
            symbol_bars = bars.data.get(symbol, [])
            volumes[symbol] = int(symbol_bars[-1].volume or 0) if symbol_bars else 0

        return volumes

    except Exception as e:
        bot_log(f"Could not get option volumes: {e}")
        return {}


def get_option_snapshots(symbols):
    if not symbols:
        return {}

    try:
        request = OptionSnapshotRequest(
            symbol_or_symbols=symbols,
            feed=_option_feed()
        )
        return option_data_client.get_option_snapshot(request)

    except Exception as e:
        bot_log(f"Could not get option snapshots: {e}")
        return {}


def bid_ask_spread_pct(quote):
    bid = _to_float(getattr(quote, "bid_price", None))
    ask = _to_float(getattr(quote, "ask_price", None))

    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None

    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None

    return (ask - bid) / midpoint


def contract_score(contract, snapshot, volume, underlying_price):
    delta = _to_float(getattr(getattr(snapshot, "greeks", None), "delta", None))
    spread_pct = bid_ask_spread_pct(getattr(snapshot, "latest_quote", None))

    if delta is None or spread_pct is None:
        return None

    if abs(delta - TARGET_DELTA) > DELTA_TOLERANCE:
        return None

    if spread_pct >= MAX_BID_ASK_SPREAD_PCT:
        return None

    dte = (contract.expiration_date - date.today()).days
    strike_distance = abs(_to_float(contract.strike_price) - underlying_price)

    return (
        abs(delta - TARGET_DELTA),
        spread_pct,
        -volume,
        dte,
        strike_distance
    )


def get_option_contract(underlying, option_type="call", min_dte=30, max_dte=60):
    today = date.today()
    min_exp = today + timedelta(days=min_dte)
    max_exp = today + timedelta(days=max_dte)
    underlying_price = get_underlying_price(underlying)

    if not underlying_price:
        bot_log(f"Could not determine underlying price for {underlying}")
        record_event("SKIP", underlying=underlying, reason="missing_underlying_price")
        return None

    contract_type = ContractType.CALL if option_type == "call" else ContractType.PUT

    request = GetOptionContractsRequest(
        underlying_symbols=[underlying],
        status=AssetStatus.ACTIVE,
        expiration_date_gte=min_exp,
        expiration_date_lte=max_exp,
        type=contract_type,
        strike_price_gte=underlying_price * 0.85,
        strike_price_lte=underlying_price * 1.15,
        limit=1000
    )

    contracts = []

    try:
        while True:
            response = trading_client.get_option_contracts(request)
            contracts.extend(response.option_contracts or [])

            if not response.next_page_token:
                break

            request.page_token = response.next_page_token

    except (APIError, RequestException) as e:
        bot_log(f"Could not get option contracts for {underlying}: {e}")
        record_event(
            "SKIP",
            underlying=underlying,
            reason="contract_lookup_failed",
            details=str(e)
        )
        return None

    if not contracts:
        bot_log(f"No option contracts found for {underlying}")
        record_event("SKIP", underlying=underlying, reason="no_contracts")
        return None

    contracts = [
        contract
        for contract in contracts
        if contract.tradable and (_to_float(contract.open_interest) or 0) > MIN_OPEN_INTEREST
    ]

    if not contracts:
        bot_log(f"No {underlying} contracts passed open interest > {MIN_OPEN_INTEREST}")
        record_event("SKIP", underlying=underlying, reason="open_interest_filter")
        return None

    symbols = [contract.symbol for contract in contracts]
    snapshots = get_option_snapshots(symbols)
    volumes = get_option_volumes(symbols)
    ranked = []

    for contract in contracts:
        volume = volumes.get(contract.symbol, 0)
        if volume <= MIN_OPTION_VOLUME:
            continue

        snapshot = snapshots.get(contract.symbol)
        if snapshot is None:
            continue

        score = contract_score(contract, snapshot, volume, underlying_price)
        if score is None:
            continue

        ranked.append((score, contract, snapshot, volume))

    if not ranked:
        bot_log(
            f"No {underlying} contracts passed volume > {MIN_OPTION_VOLUME}, "
            f"spread < {MAX_BID_ASK_SPREAD_PCT:.0%}, and delta near {TARGET_DELTA:.2f}"
        )
        record_event(
            "SKIP",
            underlying=underlying,
            reason="contract_quality_filters",
            details=(
                f"min_volume={MIN_OPTION_VOLUME};"
                f"max_spread={MAX_BID_ASK_SPREAD_PCT};"
                f"target_delta={TARGET_DELTA};"
                f"delta_tolerance={DELTA_TOLERANCE}"
            )
        )
        return None

    ranked.sort(key=lambda item: item[0])
    _, selected, selected_snapshot, selected_volume = ranked[0]
    selected_delta = _to_float(getattr(selected_snapshot.greeks, "delta", None))
    selected_spread = bid_ask_spread_pct(selected_snapshot.latest_quote)

    bot_log(f"Selected contract: {selected.symbol}")
    bot_log(f"Expiration: {selected.expiration_date}")
    bot_log(f"Strike: {selected.strike_price}")
    bot_log(f"Open interest: {selected.open_interest}")
    bot_log(f"Volume: {selected_volume}")
    bot_log(f"Bid/ask spread: {selected_spread:.2%}")
    bot_log(f"Delta: {selected_delta:.2f}")
    record_event(
        "CONTRACT_SELECTED",
        underlying=underlying,
        option_symbol=selected.symbol,
        price=underlying_price,
        details=(
            f"expiration={selected.expiration_date};"
            f"strike={selected.strike_price};"
            f"open_interest={selected.open_interest};"
            f"volume={selected_volume};"
            f"spread={selected_spread:.4f};"
            f"delta={selected_delta:.4f}"
        )
    )

    return selected.symbol


def buy_option_contract(option_symbol, qty=1, underlying=""):
    if has_open_order(option_symbol):
        bot_log(f"Open order exists for {option_symbol}. Skipping.")
        record_event(
            "SKIP",
            underlying=underlying,
            option_symbol=option_symbol,
            reason="open_order_exists"
        )
        return

    order = MarketOrderRequest(
        symbol=option_symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY
    )

    try:
        submitted_order = trading_client.submit_order(order)
        underlying_price = get_underlying_price(underlying) if underlying else ""
        bot_log(f"Placed BUY order for {qty} option contract(s): {option_symbol}")
        record_event(
            "BUY_SUBMITTED",
            underlying=underlying,
            option_symbol=option_symbol,
            qty=qty,
            price=underlying_price,
            details=f"order_id={getattr(submitted_order, 'id', '')}"
        )

    except (APIError, RequestException) as e:
        bot_log(f"Option order failed: {e}")
        record_event(
            "ORDER_FAILED",
            underlying=underlying,
            option_symbol=option_symbol,
            qty=qty,
            reason="buy_failed",
            details=str(e)
        )


def get_option_positions_for_underlying(underlying):
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        bot_log(f"Could not get positions for exits: {e}")
        return []

    return [
        position
        for position in positions
        if position.symbol != underlying and position.symbol.startswith(underlying)
    ]


def close_option_position(position, underlying, reason):
    if has_open_order(position.symbol):
        bot_log(f"Open order exists for {position.symbol}. Exit skipped.")
        return

    qty = getattr(position, "qty_available", None) or getattr(position, "qty", None)
    qty_float = _to_float(qty)

    if qty_float is not None and qty_float <= 0:
        bot_log(f"No available quantity to exit for {position.symbol}.")
        return

    try:
        close_request = ClosePositionRequest(qty=qty) if qty else None
        submitted_order = trading_client.close_position(position.symbol, close_request)
        bot_log(f"Submitted exit for {position.symbol}: {reason}")
        record_event(
            "EXIT_SUBMITTED",
            underlying=underlying,
            option_symbol=position.symbol,
            qty=qty,
            price=get_underlying_price(underlying) or "",
            reason=reason,
            details=f"order_id={getattr(submitted_order, 'id', '')}"
        )

    except (APIError, RequestException) as e:
        bot_log(f"Exit failed for {position.symbol}: {e}")
        record_event(
            "ORDER_FAILED",
            underlying=underlying,
            option_symbol=position.symbol,
            qty=qty,
            reason="exit_failed",
            details=str(e)
        )


def manage_underlying_exits(
    underlyings,
    exit_signal_func,
    stop_loss_pct,
    take_profit_pct
):
    for underlying in underlyings:
        positions = get_option_positions_for_underlying(underlying)

        if not positions:
            continue

        current_price = get_underlying_price(underlying)
        technical_exit, technical_reason = exit_signal_func(underlying)

        for position in positions:
            exit_reason = technical_reason if technical_exit else ""
            entry_price = get_latest_entry_price(underlying, position.symbol)

            if current_price and entry_price:
                change_pct = (current_price - entry_price) / entry_price

                if change_pct <= -stop_loss_pct:
                    exit_reason = f"underlying_stop_loss_{change_pct:.2%}"
                elif change_pct >= take_profit_pct:
                    exit_reason = f"underlying_take_profit_{change_pct:.2%}"

            if exit_reason:
                close_option_position(position, underlying, exit_reason)
