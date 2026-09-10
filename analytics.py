import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from config import (
    ANALYTICS_FILE,
    BOT_PERFORMANCE_START_DATE,
    BOT_STRATEGY_ID,
    REJECTED_TRADES_FILE,
    VIRTUAL_STARTING_CAPITAL,
)


FIELDNAMES = [
    "timestamp",
    "bot_id",
    "strategy",
    "event",
    "underlying",
    "option_symbol",
    "qty",
    "price",
    "underlying_price",
    "realized_pnl",
    "unrealized_pnl",
    "reason",
    "details",
    "order_id",
    "order_side",
    "order_status",
]

REJECTED_FIELDNAMES = [
    "timestamp", "bot_id", "strategy", "underlying", "contract_symbol",
    "call_or_put", "long_or_short", "strike", "expiration", "dte",
    "underlying_price", "bid", "ask", "mid", "spread_dollars",
    "spread_percent", "option_premium", "required_capital",
    "virtual_capital_available", "rejection_reason", "signal_score",
    "market_regime",
]


def _details_dict(value):
    return dict(
        item.split("=", 1) for item in (value or "").split(";") if "=" in item
    )


def _ensure_schema(path):
    if not path.exists():
        return
    with path.open("r", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames == FIELDNAMES:
            return
        rows = list(reader)
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def record_event(
    event,
    strategy="",
    underlying="",
    option_symbol="",
    qty="",
    price="",
    underlying_price="",
    realized_pnl="",
    unrealized_pnl="",
    reason="",
    details="",
    order_id="",
    order_side="",
    order_status="",
):
    path = Path(ANALYTICS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema(path)
    write_header = not path.exists()

    with path.open("a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)

        if write_header:
            writer.writeheader()

        writer.writerow({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "bot_id": BOT_STRATEGY_ID,
            "strategy": strategy,
            "event": event,
            "underlying": underlying,
            "option_symbol": option_symbol,
            "qty": qty,
            "price": price,
            "underlying_price": underlying_price,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "reason": reason,
            "details": details,
            "order_id": order_id,
            "order_side": order_side,
            "order_status": order_status,
        })


def record_rejected_trade(
    rejection_reason, strategy="", underlying="", contract_symbol="",
    call_or_put="call", long_or_short="long", strike="", expiration="",
    dte="", underlying_price="", bid="", ask="", mid="",
    spread_dollars="", spread_percent="", option_premium="",
    required_capital="", virtual_capital_available="", signal_score="",
    market_regime="",
):
    """Write a signal-qualified but unexecuted opportunity for research."""
    path = Path(REJECTED_TRADES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=REJECTED_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "bot_id": BOT_STRATEGY_ID,
            "strategy": strategy,
            "underlying": underlying,
            "contract_symbol": contract_symbol,
            "call_or_put": call_or_put,
            "long_or_short": long_or_short,
            "strike": strike,
            "expiration": expiration,
            "dte": dte,
            "underlying_price": underlying_price,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_dollars": spread_dollars,
            "spread_percent": spread_percent,
            "option_premium": option_premium,
            "required_capital": required_capital,
            "virtual_capital_available": (
                VIRTUAL_STARTING_CAPITAL if virtual_capital_available == ""
                else virtual_capital_available
            ),
            "rejection_reason": rejection_reason,
            "signal_score": signal_score,
            "market_regime": market_regime,
        })


def read_events():
    path = Path(ANALYTICS_FILE)
    if not path.exists():
        return []
    _ensure_schema(path)
    with path.open("r", newline="") as file:
        return list(csv.DictReader(file))


def latest_strategy_exit_date(strategy, underlying):
    latest = None
    for row in read_events():
        if (
            row.get("event") == "ORDER_FILL"
            and row.get("order_side") == "sell"
            and row.get("strategy") == strategy
            and row.get("underlying") == underlying
        ):
            try:
                day = datetime.fromisoformat(row.get("timestamp", "")).date()
            except ValueError:
                continue
            latest = max(latest, day) if latest else day
    return latest


def cooldown_active(strategy, underlying, trading_days, today=None):
    exited = latest_strategy_exit_date(strategy, underlying)
    if not exited or trading_days <= 0:
        return False
    cursor = exited
    elapsed = 0
    today = today or date.today()
    while cursor < today:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            elapsed += 1
    return elapsed < trading_days


def signal_bar_already_submitted(strategy, underlying, signal_date):
    marker = f"signal_date={signal_date}"
    return any(
        row.get("event") == "ORDER_SUBMITTED"
        and row.get("order_side") == "buy"
        and row.get("strategy") == strategy
        and row.get("underlying") == underlying
        and marker in row.get("details", "")
        for row in read_events()
    )


def get_strategy_open_lots():
    """Return net filled quantities and cost basis for each strategy contract."""
    lots = {}
    for row in read_events():
        if row.get("event") == "POSITION_MISSING":
            key = (row.get("strategy", ""), row.get("underlying", ""), row.get("option_symbol", ""))
            if key in lots:
                lots[key].update(qty=0.0, cost=0.0, underlying_cost=0.0, opened_at="")
            continue
        if row.get("event") != "ORDER_FILL":
            continue
        strategy = row.get("strategy", "")
        symbol = row.get("option_symbol", "")
        if not strategy or not symbol:
            continue
        key = (strategy, row.get("underlying", ""), symbol)
        bucket = lots.setdefault(key, {
            "qty": 0.0, "cost": 0.0, "underlying_cost": 0.0, "opened_at": ""
        })
        qty = float(row.get("qty") or 0)
        price = float(row.get("price") or 0)
        if row.get("order_side") == "buy":
            if bucket["qty"] <= 0:
                bucket["opened_at"] = row.get("timestamp", "")
            bucket["cost"] += qty * price
            bucket["underlying_cost"] += qty * float(row.get("underlying_price") or 0)
            bucket["qty"] += qty
        elif row.get("order_side") == "sell" and bucket["qty"] > 0:
            average = bucket["cost"] / bucket["qty"]
            underlying_average = bucket["underlying_cost"] / bucket["qty"]
            closed_qty = min(qty, bucket["qty"])
            bucket["qty"] -= closed_qty
            bucket["cost"] -= closed_qty * average
            bucket["underlying_cost"] -= closed_qty * underlying_average
            if bucket["qty"] <= 0:
                bucket["opened_at"] = ""
    return {key: value for key, value in lots.items() if value["qty"] > 0}


def get_underlying_high_water_marks():
    """Rebuild open-lot stock-price highs from the durable event ledger."""
    positions = {}
    high_water_marks = {}
    for row in read_events():
        strategy = row.get("strategy", "")
        underlying = row.get("underlying", "")
        symbol = row.get("option_symbol", "")
        if not strategy or not underlying or not symbol:
            continue
        key = (strategy, underlying, symbol)
        if row.get("event") == "POSITION_MISSING":
            positions[key] = 0.0
            high_water_marks.pop(key, None)
            continue
        if row.get("event") == "ORDER_FILL":
            qty = float(row.get("qty") or 0)
            if row.get("order_side") == "buy":
                if positions.get(key, 0) <= 0:
                    high_water_marks.pop(key, None)
                positions[key] = positions.get(key, 0) + qty
            elif row.get("order_side") == "sell":
                positions[key] = max(positions.get(key, 0) - qty, 0)
                if positions[key] <= 0:
                    high_water_marks.pop(key, None)
        if positions.get(key, 0) <= 0:
            continue
        price = float(row.get("underlying_price") or 0)
        if price > 0 and row.get("event") in {"ORDER_FILL", "RISK_SNAPSHOT"}:
            high_water_marks[key] = max(high_water_marks.get(key, price), price)
    return high_water_marks


def get_submitted_orders():
    """Return strategy orders that still need their fills reconciled."""
    submitted = {}
    filled = set()
    for row in read_events():
        order_id = row.get("order_id", "")
        if not order_id:
            continue
        if row.get("event") == "ORDER_SUBMITTED":
            submitted[order_id] = row
        elif row.get("event") in {"ORDER_FILL", "ORDER_TERMINAL"}:
            filled.add(order_id)
    return {order_id: row for order_id, row in submitted.items() if order_id not in filled}


def get_virtual_cash_available(strategy=None):
    """Return long-call virtual cash after fills and pending buy reservations."""
    cash = float(VIRTUAL_STARTING_CAPITAL)
    cutoff = date.fromisoformat(BOT_PERFORMANCE_START_DATE)
    for row in read_events():
        try:
            event_date = datetime.fromisoformat(
                row.get("timestamp", "").replace("Z", "+00:00")
            ).date()
        except (TypeError, ValueError):
            event_date = cutoff
        if event_date < cutoff:
            continue
        if strategy is not None and row.get("strategy", "") != strategy:
            continue
        qty = float(row.get("qty") or 0)
        price = float(row.get("price") or 0)
        if row.get("event") == "ORDER_FILL":
            if row.get("order_side") == "buy":
                cash -= qty * price * 100
            elif row.get("order_side") == "sell":
                cash += qty * price * 100
    for row in get_submitted_orders().values():
        if row.get("order_side") != "buy":
            continue
        if strategy is not None and row.get("strategy", "") != strategy:
            continue
        cash -= float(row.get("qty") or 0) * float(row.get("price") or 0) * 100
    return cash


def get_owned_option_symbols():
    """Return contracts that this bot has submitted buys for.

    Ownership remains recorded after an exit so an account-level position in an
    unrelated contract is never accidentally adopted by this bot.
    """
    path = Path(ANALYTICS_FILE)
    if not path.exists():
        return set()
    _ensure_schema(path)

    with path.open("r", newline="") as file:
        return {
            row.get("option_symbol", "")
            for row in csv.DictReader(file)
            if row.get("event") in {"BUY_SUBMITTED", "ORDER_SUBMITTED"}
            and row.get("bot_id", "") in {"", BOT_STRATEGY_ID}
            and (row.get("order_side") or "buy") == "buy"
            and row.get("option_symbol")
        }


def summarize_results():
    """Aggregate actual fills into independent strategy P/L buckets."""
    results = {"by_strategy": {}, "by_contract": {}, "by_underlying": {}}
    inventory = {}
    latest_prices = {}
    for row in read_events():
        symbol = row.get("option_symbol", "")
        if row.get("event") == "POSITION_SNAPSHOT" and symbol:
            latest_prices[symbol] = float(row.get("price") or 0)
        if row.get("event") == "POSITION_MISSING":
            key = (row.get("strategy", ""), row.get("underlying", ""), symbol)
            if key in inventory:
                inventory[key].update(qty=0.0, cost=0.0)
            continue
        if row.get("event") != "ORDER_FILL":
            continue
        strategy = row.get("strategy", "")
        underlying = row.get("underlying", "")
        key = (strategy, underlying, symbol)
        lot = inventory.setdefault(key, {"qty": 0.0, "cost": 0.0})
        qty = float(row.get("qty") or 0)
        price = float(row.get("price") or 0)
        if row.get("order_side") == "buy":
            lot["qty"] += qty
            lot["cost"] += qty * price
            continue
        if row.get("order_side") != "sell" or lot["qty"] <= 0:
            continue
        closed_qty = min(qty, lot["qty"])
        average = lot["cost"] / lot["qty"]
        pnl = (price - average) * closed_qty * 100
        lot["qty"] -= closed_qty
        lot["cost"] -= average * closed_qty
        for group, group_key in (
            ("by_strategy", strategy), ("by_contract", symbol), ("by_underlying", underlying)
        ):
            bucket = results[group].setdefault(
                group_key, {"realized_pnl": 0.0, "unrealized_pnl": 0.0}
            )
            bucket["realized_pnl"] += pnl

    for (strategy, underlying, symbol), lot in inventory.items():
        if lot["qty"] <= 0 or symbol not in latest_prices:
            continue
        pnl = (latest_prices[symbol] * lot["qty"] - lot["cost"]) * 100
        for group, group_key in (
            ("by_strategy", strategy), ("by_contract", symbol), ("by_underlying", underlying)
        ):
            bucket = results[group].setdefault(
                group_key, {"realized_pnl": 0.0, "unrealized_pnl": 0.0}
            )
            bucket["unrealized_pnl"] += pnl
    return results


def summarize_performance_since(start_date, current_prices=None, strategy=None):
    """Return bot-only fill performance from a fixed date onward.

    Return percentage uses this bot's virtual allocation, never Alpaca account
    equity. Capital employed for a long call is the premium paid.
    """
    cutoff = (
        start_date if isinstance(start_date, date)
        else date.fromisoformat(str(start_date))
    )
    current_prices = current_prices or {}
    events = read_events()
    submitted_metadata = {
        row.get("order_id", ""): _details_dict(row.get("details", ""))
        for row in events
        if row.get("event") == "ORDER_SUBMITTED" and row.get("order_id")
    }
    inventory = {}
    deployed_premium = 0.0
    realized_pnl = 0.0
    entry_premiums = []
    closed_pnls = []
    holding_days = []
    current_capital_employed = 0.0
    maximum_capital_employed = 0.0
    realized_equity = float(VIRTUAL_STARTING_CAPITAL)
    equity_peak = realized_equity
    maximum_drawdown = 0.0
    entry_dtes = []
    entry_spreads = []
    expired_worthless = 0

    for row in events:
        try:
            event_date = datetime.fromisoformat(
                row.get("timestamp", "").replace("Z", "+00:00")
            ).date()
        except (TypeError, ValueError):
            continue
        if event_date < cutoff:
            continue

        symbol = row.get("option_symbol", "")
        key = (row.get("strategy", ""), row.get("underlying", ""), symbol)
        if row.get("event") == "POSITION_MISSING":
            inventory.pop(key, None)
            continue
        if row.get("event") != "ORDER_FILL" or not symbol:
            continue
        if strategy is not None and row.get("strategy", "") != strategy:
            continue

        qty = float(row.get("qty") or 0)
        price = float(row.get("price") or 0)
        lot = inventory.setdefault(
            key, {"qty": 0.0, "cost": 0.0, "opened_at": None}
        )
        if row.get("order_side") == "buy":
            premium = qty * price * 100
            if lot["qty"] <= 0:
                lot["opened_at"] = event_date
            lot["qty"] += qty
            lot["cost"] += premium
            deployed_premium += premium
            entry_premiums.append(premium)
            metadata = submitted_metadata.get(row.get("order_id", ""), {})
            try:
                entry_dtes.append(float(metadata.get("dte", "")))
            except (TypeError, ValueError):
                match = re.match(r"^[A-Z.]+(\d{6})[CP]\d{8}$", symbol)
                if match:
                    expiration = datetime.strptime(match.group(1), "%y%m%d").date()
                    entry_dtes.append((expiration - event_date).days)
            try:
                entry_spreads.append(float(metadata.get("spread_pct", "")))
            except (TypeError, ValueError):
                pass
            current_capital_employed += premium
            maximum_capital_employed = max(
                maximum_capital_employed, current_capital_employed
            )
        elif row.get("order_side") == "sell" and lot["qty"] > 0:
            closed_qty = min(qty, lot["qty"])
            average_cost = lot["cost"] / lot["qty"]
            closed_cost = closed_qty * average_cost
            pnl = closed_qty * price * 100 - closed_cost
            if closed_qty * price * 100 <= 0.01:
                expired_worthless += 1
            realized_pnl += pnl
            closed_pnls.append(pnl)
            current_capital_employed = max(
                current_capital_employed - closed_cost, 0.0
            )
            if lot["opened_at"]:
                holding_days.append((event_date - lot["opened_at"]).days)
            lot["qty"] -= closed_qty
            lot["cost"] -= closed_cost
            if lot["qty"] <= 0:
                lot["opened_at"] = None
            realized_equity += pnl
            equity_peak = max(equity_peak, realized_equity)
            maximum_drawdown = max(maximum_drawdown, equity_peak - realized_equity)

    unrealized_pnl = 0.0
    positions_value = 0.0
    open_positions = 0
    for (_, _, symbol), lot in inventory.items():
        current = current_prices.get(symbol)
        if lot["qty"] <= 0:
            continue
        open_positions += 1
        if current is not None:
            market_value = lot["qty"] * float(current) * 100
            positions_value += market_value
            unrealized_pnl += market_value - lot["cost"]

    total_pnl = realized_pnl + unrealized_pnl
    wins = [value for value in closed_pnls if value > 0]
    losses = [value for value in closed_pnls if value < 0]
    gross_loss = abs(sum(losses))
    return_pct = total_pnl / VIRTUAL_STARTING_CAPITAL * 100
    return_on_capital_employed = (
        total_pnl / deployed_premium * 100 if deployed_premium > 0 else 0.0
    )
    return {
        "start_date": cutoff.isoformat(),
        "starting_virtual_capital": VIRTUAL_STARTING_CAPITAL,
        "ending_virtual_capital": VIRTUAL_STARTING_CAPITAL + total_pnl,
        "deployed_premium": deployed_premium,
        "premium_paid": deployed_premium,
        "premium_lost": abs(sum(losses)),
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "return_pct": return_pct,
        "return_on_capital_employed": return_on_capital_employed,
        "option_return_pct": return_on_capital_employed,
        "average_capital_employed_per_trade": (
            sum(entry_premiums) / len(entry_premiums) if entry_premiums else 0.0
        ),
        "maximum_capital_employed": maximum_capital_employed,
        "capital_employed": current_capital_employed,
        "trade_count": len(closed_pnls),
        "win_rate": len(wins) / len(closed_pnls) if closed_pnls else 0.0,
        "average_winner": sum(wins) / len(wins) if wins else 0.0,
        "average_loser": sum(losses) / len(losses) if losses else 0.0,
        "expectancy": (
            sum(closed_pnls) / len(closed_pnls) if closed_pnls else 0.0
        ),
        "profit_factor": sum(wins) / gross_loss if gross_loss else float("inf"),
        "max_drawdown": maximum_drawdown,
        "average_hold_days": (
            sum(holding_days) / len(holding_days) if holding_days else 0.0
        ),
        "largest_winner": max(wins) if wins else 0.0,
        "largest_loser": min(losses) if losses else 0.0,
        "average_option_premium": (
            sum(entry_premiums) / len(entry_premiums) if entry_premiums else 0.0
        ),
        "average_dte": sum(entry_dtes) / len(entry_dtes) if entry_dtes else 0.0,
        "average_spread_pct": (
            sum(entry_spreads) / len(entry_spreads) * 100 if entry_spreads else 0.0
        ),
        "expired_worthless": expired_worthless,
        "open_positions": open_positions,
        "positions_value": positions_value,
    }


def build_strategy_report(strategy_names=()):
    """Build fill-based paper performance and open-position details by strategy."""
    events = read_events()
    latest_prices = {}
    pending = get_submitted_orders()
    report = {
        name: {
            "completed_trades": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "open_positions": [],
            "pending_orders": 0,
        }
        for name in strategy_names
    }
    inventory = {}

    for row in events:
        symbol = row.get("option_symbol", "")
        if row.get("event") == "POSITION_SNAPSHOT" and symbol:
            latest_prices[symbol] = float(row.get("price") or 0)
        if row.get("event") == "POSITION_MISSING":
            key = (row.get("strategy", ""), row.get("underlying", ""), symbol)
            if key in inventory:
                inventory[key].update(qty=0.0, cost=0.0)
            continue
        if row.get("event") != "ORDER_FILL":
            continue
        strategy = row.get("strategy", "")
        if not strategy:
            continue
        stats = report.setdefault(strategy, {
            "completed_trades": 0, "wins": 0, "losses": 0,
            "realized_pnl": 0.0, "unrealized_pnl": 0.0,
            "open_positions": [], "pending_orders": 0,
        })
        key = (strategy, row.get("underlying", ""), symbol)
        lot = inventory.setdefault(key, {"qty": 0.0, "cost": 0.0})
        qty = float(row.get("qty") or 0)
        price = float(row.get("price") or 0)
        if row.get("order_side") == "buy":
            lot["qty"] += qty
            lot["cost"] += qty * price
        elif row.get("order_side") == "sell" and lot["qty"] > 0:
            closed_qty = min(qty, lot["qty"])
            average = lot["cost"] / lot["qty"]
            pnl = (price - average) * closed_qty * 100
            lot["qty"] -= closed_qty
            lot["cost"] -= average * closed_qty
            stats["completed_trades"] += 1
            stats["realized_pnl"] += pnl
            if pnl > 0:
                stats["wins"] += 1
            elif pnl < 0:
                stats["losses"] += 1

    for (strategy, underlying, symbol), lot in inventory.items():
        if lot["qty"] <= 0:
            continue
        current = latest_prices.get(symbol)
        average = lot["cost"] / lot["qty"]
        unrealized = None
        if current is not None:
            unrealized = (current - average) * lot["qty"] * 100
            report[strategy]["unrealized_pnl"] += unrealized
        report[strategy]["open_positions"].append({
            "underlying": underlying,
            "option_symbol": symbol,
            "qty": lot["qty"],
            "average_entry_price": average,
            "current_price": current,
            "unrealized_pnl": unrealized,
        })

    for row in pending.values():
        strategy = row.get("strategy", "")
        if strategy:
            report.setdefault(strategy, {
                "completed_trades": 0, "wins": 0, "losses": 0,
                "realized_pnl": 0.0, "unrealized_pnl": 0.0,
                "open_positions": [], "pending_orders": 0,
            })["pending_orders"] += 1
    return report


def get_latest_entry_price(underlying, option_symbol):
    path = Path(ANALYTICS_FILE)
    if not path.exists():
        return None
    _ensure_schema(path)

    latest_price = None

    with path.open("r", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if (
                row.get("event") == "BUY_SUBMITTED"
                and row.get("underlying") == underlying
                and row.get("option_symbol") == option_symbol
            ):
                try:
                    latest_price = float(row.get("price") or 0)
                except ValueError:
                    latest_price = None

    return latest_price
