from datetime import date, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
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

from config import API_KEY, SECRET_KEY, ALPACA_PAPER

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=ALPACA_PAPER)


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


def get_option_contract(underlying, option_type="call", min_dte=30, max_dte=60):
    today = date.today()
    min_exp = today + timedelta(days=min_dte)
    max_exp = today + timedelta(days=max_dte)

    contract_type = ContractType.CALL if option_type == "call" else ContractType.PUT

    request = GetOptionContractsRequest(
        underlying_symbols=[underlying],
        status=AssetStatus.ACTIVE,
        expiration_date_gte=min_exp,
        expiration_date_lte=max_exp,
        type=contract_type
    )

    response = trading_client.get_option_contracts(request)

    contracts = response.option_contracts

    if not contracts:
        print(f"No option contracts found for {underlying}")
        return None

    # Basic first version: choose nearest expiration, then closest ATM-ish contract.
    # Later we can improve this with delta/liquidity filters.
    contracts = sorted(
        contracts,
        key=lambda c: (c.expiration_date, abs(float(c.strike_price)))
    )

    selected = contracts[0]

    print(f"Selected contract: {selected.symbol}")
    print(f"Expiration: {selected.expiration_date}")
    print(f"Strike: {selected.strike_price}")

    return selected.symbol


def buy_option_contract(option_symbol, qty=1):
    if has_open_order(option_symbol):
        print(f"Open order exists for {option_symbol}. Skipping.")
        return

    order = MarketOrderRequest(
        symbol=option_symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY
    )

    try:
        trading_client.submit_order(order)
        print(f"Placed BUY order for {qty} option contract(s): {option_symbol}")

    except APIError as e:
        print(f"Option order failed: {e}")