"""Single import point that registers every ORM model with the metadata.

Alembic autogenerate and DatabaseManager.create_tables both import this module.
"""
from src.instruments.database.db_models.instrument_model import Instrument  # noqa: F401
from src.notes.database.db_models.trade_note_model import TradeNote  # noqa: F401
from src.orders.database.db_models.order_model import (  # noqa: F401
    Order,
    OrderCharge,
    OrderEvent,
    OrderFill,
)
from src.positions.database.db_models.position_model import Position  # noqa: F401
from src.settings.database.db_models.app_setting_model import AppSetting  # noqa: F401

__all__ = [
    "Instrument",
    "Order",
    "OrderEvent",
    "OrderFill",
    "OrderCharge",
    "Position",
    "TradeNote",
    "AppSetting",
]
