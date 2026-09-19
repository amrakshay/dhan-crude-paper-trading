"""Single import point that registers every ORM model with the metadata.

Alembic autogenerate and DatabaseManager.create_tables both import this module.
"""
from src.btst.database.db_models.btst_session_model import (  # noqa: F401
    BtstDecision,
    BtstHolding,
    BtstSession,
)
from src.chart_trading.database.db_models.chart_trade_model import ChartTrade  # noqa: F401
from src.connections.database.db_models.alert_model import Alert  # noqa: F401
from src.connections.database.db_models.connection_model import (  # noqa: F401
    Connection,
    ConnectionSetting,
)
from src.daily_bars.database.db_models.daily_bar_model import DailyBar  # noqa: F401
from src.instruments.database.db_models.instrument_model import Instrument  # noqa: F401
from src.notes.database.db_models.trade_note_model import TradeNote  # noqa: F401
from src.orders.database.db_models.order_model import (  # noqa: F401
    Order,
    OrderCharge,
    OrderEvent,
    OrderFill,
)
from src.portfolios.database.db_models.portfolio_model import (  # noqa: F401
    CashLedgerEntry,
    Portfolio,
    PortfolioStrategy,
)
from src.positions.database.db_models.position_model import Position  # noqa: F401
from src.settings.database.db_models.app_setting_model import AppSetting  # noqa: F401
from src.strategies.database.db_models.feature_toggle_model import (  # noqa: F401
    FeatureToggle,
)
from src.strategies.database.db_models.strategy_setting_model import (  # noqa: F401
    StrategySetting,
)
from src.swing.database.db_models.swing_session_model import (  # noqa: F401
    SwingDecision,
    SwingSession,
)
from src.swing.database.db_models.swing_stop_model import SwingStop  # noqa: F401
from src.users.database.db_models.user_model import User  # noqa: F401

__all__ = [
    "Alert",
    "BtstDecision",
    "BtstHolding",
    "BtstSession",
    "ChartTrade",
    "Connection",
    "ConnectionSetting",
    "DailyBar",
    "Instrument",
    "Order",
    "OrderEvent",
    "OrderFill",
    "OrderCharge",
    "Portfolio",
    "PortfolioStrategy",
    "CashLedgerEntry",
    "Position",
    "TradeNote",
    "AppSetting",
    "FeatureToggle",
    "SwingSession",
    "SwingDecision",
    "SwingStop",
    "User",
]
