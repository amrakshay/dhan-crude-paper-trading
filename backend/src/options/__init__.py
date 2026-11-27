"""The shared NSE options ENGINE (OPTIONS_TRADER_PLAN.md section 3).

Everything here serves every NSE option position, whichever strategy holds it:
the chain view and its feed pinning today; SPAN margin, baskets, the risk desk
and expiry in later phases.

THE ENGINE NAMES NO STRATEGY. It takes a `StrategyDefinition` from its caller
and never imports a module package (`src/options_trader/` or any later
automated one) or spells a strategy key. That is asserted in
`tests/test_options_engine_boundary.py`: without the test, "shared" would be a
convention that the first convenient import quietly ends.

No router here. Routes belong to the module that offers a page.
"""
