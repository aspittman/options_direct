import csv
from datetime import datetime
from pathlib import Path

from config import ANALYTICS_FILE


FIELDNAMES = [
    "timestamp",
    "event",
    "underlying",
    "option_symbol",
    "qty",
    "price",
    "realized_pnl",
    "unrealized_pnl",
    "reason",
    "details"
]


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
    underlying="",
    option_symbol="",
    qty="",
    price="",
    realized_pnl="",
    unrealized_pnl="",
    reason="",
    details=""
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
            "event": event,
            "underlying": underlying,
            "option_symbol": option_symbol,
            "qty": qty,
            "price": price,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "reason": reason,
            "details": details
        })


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
            if row.get("event") == "BUY_SUBMITTED" and row.get("option_symbol")
        }


def summarize_results():
    """Aggregate explicitly separated realized and unrealized P/L."""
    path = Path(ANALYTICS_FILE)
    results = {"by_contract": {}, "by_underlying": {}}
    if not path.exists():
        return results
    _ensure_schema(path)

    latest_unrealized = {}
    realized = []
    with path.open("r", newline="") as file:
        for row in csv.DictReader(file):
            key = (row.get("underlying", ""), row.get("option_symbol", ""))
            if row.get("event") == "POSITION_SNAPSHOT":
                latest_unrealized[key] = float(row.get("unrealized_pnl") or 0)
            elif row.get("event") == "EXIT_SUBMITTED":
                latest_unrealized.pop(key, None)
            if row.get("realized_pnl") not in (None, ""):
                realized.append((key, float(row["realized_pnl"])))

    for (underlying, contract), pnl in realized:
        for group, key in (("by_contract", contract), ("by_underlying", underlying)):
            bucket = results[group].setdefault(key, {"realized_pnl": 0.0, "unrealized_pnl": 0.0})
            bucket["realized_pnl"] += pnl
    for (underlying, contract), pnl in latest_unrealized.items():
        for group, key in (("by_contract", contract), ("by_underlying", underlying)):
            bucket = results[group].setdefault(key, {"realized_pnl": 0.0, "unrealized_pnl": 0.0})
            bucket["unrealized_pnl"] += pnl
    return results


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
