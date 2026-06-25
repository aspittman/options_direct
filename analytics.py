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
    "reason",
    "details"
]


def record_event(
    event,
    underlying="",
    option_symbol="",
    qty="",
    price="",
    reason="",
    details=""
):
    path = Path(ANALYTICS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
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
            "reason": reason,
            "details": details
        })


def get_latest_entry_price(underlying, option_symbol):
    path = Path(ANALYTICS_FILE)
    if not path.exists():
        return None

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
