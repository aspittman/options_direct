from datetime import date, datetime, timedelta
import re

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
    TARGET_DELTA,
    ALLOW_DUPLICATE_CONTRACTS,
    ALLOW_MULTIPLE_CONTRACTS_PER_UNDERLYING,
    EXIT_DTE,
    MAX_PREMIUM_PER_TRADE,
    MAX_TOTAL_OPTION_PREMIUM,
    OPTION_STOP_LOSS_PERCENT,
    OPTION_TRAILING_STOP_PERCENT,
    require_alpaca_credentials
)
from analytics import get_latest_entry_price, get_owned_option_symbols, record_event, summarize_results
from bot_logger import bot_log

API_KEY, SECRET_KEY = require_alpaca_credentials()
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


OPTION_SYMBOL_RE = re.compile(r"^([A-Z.]+)(\d{6})([CP])(\d{8})$")
CONTRACT_MULTIPLIER = 100
_high_water_marks = {}


def parse_option_symbol(symbol):
    match = OPTION_SYMBOL_RE.match(symbol or "")
    if not match:
        return None
    underlying, expiration, option_type, strike = match.groups()
    return {
        "underlying": underlying,
        "expiration": datetime.strptime(expiration, "%y%m%d").date(),
        "option_type": "call" if option_type == "C" else "put",
        "strike": int(strike) / 1000,
    }


def get_options_direct_positions():
    try:
        owned = get_owned_option_symbols()
        return [
            position for position in trading_client.get_all_positions()
            if position.symbol in owned and parse_option_symbol(position.symbol)
        ]
    except Exception as e:
        bot_log(f"Could not retrieve OptionsDirect positions: {e}")
        return []


def get_open_positions_count():
    return len(get_options_direct_positions())


def has_open_order(symbol):
    try:
        orders = trading_client.get_orders()
        return any(order.symbol == symbol for order in orders)
    except Exception:
        return False


def already_holding_underlying(underlying):
    return any(
        parse_option_symbol(position.symbol)["underlying"] == underlying
        for position in get_options_direct_positions()
    )


def log_open_option_positions():
    positions = get_options_direct_positions()
    bot_log(f"OptionsDirect open option positions: {len(positions)}")
    for position in positions:
        parsed = parse_option_symbol(position.symbol)
        qty = _to_float(getattr(position, "qty", None)) or 0
        avg = _to_float(getattr(position, "avg_entry_price", None)) or 0
        current = _to_float(getattr(position, "current_price", None)) or 0
        market_value = _to_float(getattr(position, "market_value", None))
        if market_value is None:
            market_value = qty * current * CONTRACT_MULTIPLIER
        pnl = _to_float(getattr(position, "unrealized_pl", None)) or 0
        pnl_pct = _to_float(getattr(position, "unrealized_plpc", None)) or 0
        dte = (parsed["expiration"] - date.today()).days
        bot_log(
            "OPEN_POSITION "
            f"contract={position.symbol} underlying={parsed['underlying']} "
            f"type={parsed['option_type']} strike={parsed['strike']:.3f} "
            f"expiration={parsed['expiration']} dte={dte} qty={qty:g} "
            f"avg_entry=${avg:.2f} current=${current:.2f} market_value=${market_value:.2f} "
            f"unrealized_pl=${pnl:.2f} unrealized_pl_pct={pnl_pct:.2%}"
        )
        record_event(
            "POSITION_SNAPSHOT", underlying=parsed["underlying"],
            option_symbol=position.symbol, qty=qty, price=current,
            unrealized_pnl=pnl,
            details=f"market_value={market_value:.2f};unrealized_pct={pnl_pct:.6f};dte={dte}"
        )
    return positions


def log_analytics_summary():
    results = summarize_results()
    for grouping, buckets in results.items():
        for symbol, values in sorted(buckets.items()):
            bot_log(
                f"RESULTS {grouping}={symbol} realized_pl=${values['realized_pnl']:.2f} "
                f"unrealized_pl=${values['unrealized_pnl']:.2f}"
            )


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
    greeks = getattr(selected_snapshot, "greeks", None)
    quote = getattr(selected_snapshot, "latest_quote", None)
    bid = _to_float(getattr(quote, "bid_price", None)) or 0
    ask = _to_float(getattr(quote, "ask_price", None)) or 0
    midpoint = (bid + ask) / 2
    spread_dollars = ask - bid
    selected_spread = bid_ask_spread_pct(quote) or 0
    selected_delta = _to_float(getattr(greeks, "delta", None))
    gamma = _to_float(getattr(greeks, "gamma", None))
    theta = _to_float(getattr(greeks, "theta", None))
    iv = _to_float(getattr(selected_snapshot, "implied_volatility", None))
    dte = (selected.expiration_date - today).days
    selection_reason = (
        "best rank by delta distance, spread, volume, DTE, and strike distance "
        "after unchanged liquidity and Greek filters"
    )
    bot_log(
        "CONTRACT_SELECTED "
        f"underlying={underlying} underlying_price=${underlying_price:.2f} "
        f"contract={selected.symbol} strike={float(selected.strike_price):.3f} "
        f"expiration={selected.expiration_date} dte={dte} bid=${bid:.2f} ask=${ask:.2f} "
        f"midpoint=${midpoint:.2f} spread_dollars=${spread_dollars:.2f} "
        f"spread_pct={selected_spread:.2%} delta={selected_delta:.4f} "
        f"gamma={gamma if gamma is not None else 'N/A'} theta={theta if theta is not None else 'N/A'} "
        f"iv={iv if iv is not None else 'N/A'} volume={selected_volume} "
        f"open_interest={selected.open_interest} reason=\"{selection_reason}\""
    )
    record_event(
        "CONTRACT_SELECTED",
        underlying=underlying,
        option_symbol=selected.symbol,
        price=underlying_price,
        details=(
            f"underlying_price={underlying_price};expiration={selected.expiration_date};dte={dte};"
            f"strike={selected.strike_price};"
            f"open_interest={selected.open_interest};"
            f"volume={selected_volume};"
            f"bid={bid};ask={ask};midpoint={midpoint};spread_dollars={spread_dollars};"
            f"spread_pct={selected_spread:.6f};delta={selected_delta:.6f};"
            f"gamma={gamma};theta={theta};iv={iv};selection_reason={selection_reason}"
        )
    )

    return selected.symbol


def buy_option_contract(option_symbol, qty=1, underlying=""):
    positions = get_options_direct_positions()
    parsed = parse_option_symbol(option_symbol)
    held_symbols = {position.symbol for position in positions}
    try:
        bot_orders = [
            order for order in trading_client.get_orders()
            if str(getattr(order, "client_order_id", "")).startswith("optionsdirect-")
        ]
    except Exception:
        bot_orders = []
    pending_symbols = {getattr(order, "symbol", "") for order in bot_orders}
    if not ALLOW_DUPLICATE_CONTRACTS and option_symbol in held_symbols | pending_symbols:
        bot_log(f"Duplicate contract blocked: {option_symbol}")
        record_event("SKIP", underlying=underlying, option_symbol=option_symbol, reason="duplicate_contract")
        return
    if (
        not ALLOW_MULTIPLE_CONTRACTS_PER_UNDERLYING
        and parsed
        and any(
            parsed_order and parsed_order["underlying"] == parsed["underlying"]
            for parsed_order in (
                parse_option_symbol(symbol) for symbol in held_symbols | pending_symbols
            )
        )
    ):
        bot_log(f"Additional contract for {underlying} blocked by configuration.")
        record_event("SKIP", underlying=underlying, option_symbol=option_symbol, reason="multiple_underlying_contracts")
        return

    if has_open_order(option_symbol):
        bot_log(f"Open order exists for {option_symbol}. Skipping.")
        record_event(
            "SKIP",
            underlying=underlying,
            option_symbol=option_symbol,
            reason="open_order_exists"
        )
        return

    snapshots = get_option_snapshots([option_symbol])
    snapshot = snapshots.get(option_symbol)
    quote = getattr(snapshot, "latest_quote", None)
    bid = _to_float(getattr(quote, "bid_price", None)) or 0
    ask = _to_float(getattr(quote, "ask_price", None)) or 0
    estimated_price = (bid + ask) / 2 if bid > 0 and ask > 0 else ask
    estimated_premium = estimated_price * qty * CONTRACT_MULTIPLIER
    current_total = sum(
        (_to_float(getattr(p, "avg_entry_price", None)) or 0)
        * abs(_to_float(getattr(p, "qty", None)) or 0) * CONTRACT_MULTIPLIER
        for p in positions
    )
    if estimated_premium <= 0:
        bot_log(f"Cannot price {option_symbol} for premium risk checks. Skipping.")
        record_event("SKIP", underlying=underlying, option_symbol=option_symbol, reason="missing_option_price")
        return
    if estimated_premium > MAX_PREMIUM_PER_TRADE:
        bot_log(f"Premium limit blocked {option_symbol}: estimated=${estimated_premium:.2f}, MAX_PREMIUM_PER_TRADE=${MAX_PREMIUM_PER_TRADE:.2f}")
        record_event("SKIP", underlying=underlying, option_symbol=option_symbol, reason="max_premium_per_trade", details=f"estimated_premium={estimated_premium:.2f}")
        return
    if current_total + estimated_premium > MAX_TOTAL_OPTION_PREMIUM:
        bot_log(f"Total premium limit blocked {option_symbol}: current=${current_total:.2f}, proposed=${estimated_premium:.2f}, MAX_TOTAL_OPTION_PREMIUM=${MAX_TOTAL_OPTION_PREMIUM:.2f}")
        record_event("SKIP", underlying=underlying, option_symbol=option_symbol, reason="max_total_option_premium")
        return

    order = MarketOrderRequest(
        symbol=option_symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id=f"optionsdirect-{option_symbol}-{int(datetime.now().timestamp())}"
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
            details=(f"order_id={getattr(submitted_order, 'id', '')};"
                     f"underlying_price={underlying_price};estimated_premium={estimated_premium:.2f}")
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
    return [
        position for position in get_options_direct_positions()
        if parse_option_symbol(position.symbol)["underlying"] == underlying
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
            unrealized_pnl=_to_float(getattr(position, "unrealized_pl", None)) or 0,
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
            parsed = parse_option_symbol(position.symbol)
            dte = (parsed["expiration"] - date.today()).days
            option_price = _to_float(getattr(position, "current_price", None)) or 0
            option_plpc = _to_float(getattr(position, "unrealized_plpc", None)) or 0
            high_water = max(_high_water_marks.get(position.symbol, option_price), option_price)
            _high_water_marks[position.symbol] = high_water

            if dte <= EXIT_DTE:
                exit_reason = f"expiration_management_dte_{dte}"
            elif option_plpc <= -OPTION_STOP_LOSS_PERCENT:
                exit_reason = f"option_stop_loss_{option_plpc:.2%}"
            elif (
                OPTION_TRAILING_STOP_PERCENT > 0
                and high_water > 0
                and option_price <= high_water * (1 - OPTION_TRAILING_STOP_PERCENT)
            ):
                drawdown = (option_price - high_water) / high_water
                exit_reason = f"option_trailing_stop_{drawdown:.2%}"

            if current_price and entry_price:
                change_pct = (current_price - entry_price) / entry_price

                if change_pct <= -stop_loss_pct:
                    exit_reason = f"underlying_stop_loss_{change_pct:.2%}"
                elif change_pct >= take_profit_pct:
                    exit_reason = f"underlying_take_profit_{change_pct:.2%}"

            if exit_reason:
                close_option_position(position, underlying, exit_reason)

    open_symbols = {position.symbol for position in get_options_direct_positions()}
    for symbol in list(_high_water_marks):
        if symbol not in open_symbols:
            del _high_water_marks[symbol]
