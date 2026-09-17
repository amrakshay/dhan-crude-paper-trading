"""Reading a persisted charge breakdown back out.

`order_charges` keeps `turnover`, `total_charges` and `rates_version` as real
columns because they are queried and aggregated. The LINE ITEMS live in
`breakdown_json`, because which line items exist is a property of the rate card
an order was charged under -- an MCX order has `ctt`, an NSE equity order would
have `stt` -- and a fixed column per tax is exactly what would make adding one a
schema migration.

Everything that needs to read those line items back (the API, the reports, the
CSV exports) comes through here, so there is one parser rather than four.
"""
import json
from decimal import Decimal
from typing import Any, Dict, List

from src.logging_config import get_logger

logger = get_logger("charges.persistence")

ZERO = Decimal("0")


def charge_component_rows(charge) -> List[Dict[str, Any]]:
    """The stored line items for one order, as plain dicts.

    A row whose breakdown cannot be parsed yields no line items rather than
    invented ones: its `total_charges` is still a real number, and showing a
    total with no breakdown is honest in a way that fabricating a breakdown
    would not be.
    """
    raw = getattr(charge, "breakdown_json", None)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Unparseable charge breakdown on order_id=%s; reporting the total "
            "with no line items",
            getattr(charge, "order_id", None),
        )
        return []
    if not isinstance(parsed, list):
        return []
    return [row for row in parsed if isinstance(row, dict) and row.get("name")]


def charge_components(charge) -> Dict[str, Decimal]:
    """{component name: amount} for one order's charges."""
    totals: Dict[str, Decimal] = {}
    for row in charge_component_rows(charge):
        try:
            amount = Decimal(str(row.get("amount", "0")))
        except Exception:  # noqa: BLE001 - a bad amount must not kill a report
            continue
        name = str(row["name"])
        totals[name] = totals.get(name, ZERO) + amount
    return totals


def component_labels(charges: List[Any]) -> Dict[str, str]:
    """{name: label} across a set of charge rows, for column headings."""
    labels: Dict[str, str] = {}
    for charge in charges:
        for row in charge_component_rows(charge):
            name = str(row["name"])
            labels.setdefault(
                name, str(row.get("label") or name.replace("_", " ").capitalize())
            )
    return labels
