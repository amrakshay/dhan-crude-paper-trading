"""The NSE Options Trader: the discretionary page on the shared options engine.

`conf/strategies/nse-options-trader.yaml` is its module definition. It owns the
page's routes and nothing else: the chain, and later margin, baskets, the risk
desk and expiry, are the ENGINE's (`src/options/`), which this package calls
and which never calls back. Orders go through the ordinary `/api/orders`
endpoint with `product` set, so the page has no privileged order path.
"""
from src.options_trader.routes.options_trader_router import options_trader_router

options_trader_main_router = options_trader_router

__all__ = ["options_trader_main_router", "options_trader_router"]
