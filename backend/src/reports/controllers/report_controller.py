"""P&L report orchestration, including CSV export."""
import csv
import io
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.charges.services.charge_persistence import charge_components, component_labels
from src.core.time_utils import to_ist
from src.logging_config import get_logger
from src.orders.database.db_operations.order_repository import OrderRepository
from src.reports.api_schemas.report_schemas import (
    BucketResponse,
    EquityPointResponse,
    PnlReportResponse,
    RealisationResponse,
)
from src.reports.services.pnl_service import PnlService

logger = get_logger("reports.controller")


class ReportController:
    def __init__(self, session: AsyncSession, book=None):
        self.session = session
        self.service = PnlService(OrderRepository(session), book=book)

    async def build_report(
        self,
        security_id: Optional[str] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        strategy_key: Optional[str] = None,
    ) -> PnlReportResponse:
        report = await self.service.build_report(
            security_id, placed_from, placed_to, strategy_key
        )
        logger.debug(
            "Built P&L report (security=%s, from=%s, to=%s): realised gross %s, "
            "charges %s, net %s",
            security_id or "all", placed_from, placed_to,
            report.realised_gross, report.total_charges, report.realised_net,
        )

        return PnlReportResponse(
            realisedGross=report.realised_gross,
            totalCharges=report.total_charges,
            realisedNet=report.realised_net,
            unrealised=report.unrealised,
            netIncludingUnrealised=report.net_including_unrealised,
            chargeComponents=report.charge_components,
            byDay=[BucketResponse(**bucket) for bucket in report.by_day],
            byExpiry=[BucketResponse(**bucket) for bucket in report.by_expiry],
            byStrike=[BucketResponse(**bucket) for bucket in report.by_strike],
            equityCurve=[EquityPointResponse(**point) for point in report.equity_curve],
            realisations=[
                RealisationResponse(
                    at=to_ist(event.at).isoformat(),
                    securityId=event.security_id,
                    tradingSymbol=event.trading_symbol,
                    expiryDate=event.expiry_date.isoformat() if event.expiry_date else None,
                    strikePrice=event.strike_price,
                    optionType=event.option_type,
                    quantity=event.quantity,
                    entryPrice=event.entry_price,
                    exitPrice=event.exit_price,
                    grossPnl=event.gross_pnl,
                    orderId=event.order_id,
                )
                for event in report.events
            ],
            tradeCount=report.trade_count,
            winCount=report.win_count,
            lossCount=report.loss_count,
            openPositionsWithoutMarks=report.open_positions_without_marks,
        )

    async def export_realisations_csv(
        self,
        security_id: Optional[str] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        strategy_key: Optional[str] = None,
    ) -> str:
        report = await self.service.build_report(
            security_id, placed_from, placed_to, strategy_key
        )
        logger.info(
            "Exporting realisations CSV (security=%s, from=%s, to=%s)",
            security_id or "all", placed_from, placed_to,
        )

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "realised_at_ist", "trading_symbol", "security_id", "expiry",
                "strike", "option_type", "quantity", "entry_price", "exit_price",
                "gross_pnl", "order_id",
            ]
        )
        for event in report.events:
            writer.writerow(
                [
                    to_ist(event.at).isoformat(),
                    event.trading_symbol,
                    event.security_id,
                    event.expiry_date.isoformat() if event.expiry_date else "",
                    event.strike_price if event.strike_price is not None else "",
                    event.option_type or "",
                    event.quantity,
                    event.entry_price,
                    event.exit_price,
                    event.gross_pnl,
                    event.order_id,
                ]
            )
        return buffer.getvalue()

    async def export_orders_csv(
        self,
        security_id: Optional[str] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        strategy_key: Optional[str] = None,
    ) -> str:
        """Every order with its full charges breakdown, one row each."""
        logger.info(
            "Exporting orders CSV (security=%s, from=%s, to=%s)",
            security_id or "all", placed_from, placed_to,
        )
        repository = OrderRepository(self.session)
        orders, _total = await repository.list_orders(
            security_id=security_id,
            strategy_key=strategy_key,
            placed_from=placed_from,
            placed_to=placed_to,
            page=0,
            size=100000,
        )
        charges = await repository.list_charges_for_orders([order.id for order in orders])

        # The charge columns are whatever line items the orders in this export
        # actually carry, in a stable order, rather than a fixed list of the
        # taxes MCX happens to levy. An export spanning two rate cards shows
        # both cards' taxes, and a card that adds one needs no change here.
        labels = component_labels(list(charges.values()))
        component_names = sorted(labels)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "placed_at_ist", "order_id", "client_order_id", "strategy_key",
                "trading_symbol",
                "security_id", "expiry", "strike", "option_type", "side",
                "order_type", "lots", "quantity", "limit_price", "status",
                "filled_quantity", "average_fill_price", "turnover",
                *component_names,
                "total_charges", "rates_version",
                "rejection_reason",
            ]
        )
        for order in orders:
            charge = charges.get(order.id)
            amounts = charge_components(charge) if charge else {}
            writer.writerow(
                [
                    to_ist(order.placed_at).isoformat(),
                    order.id,
                    order.client_order_id,
                    order.strategy_key,
                    order.trading_symbol,
                    order.security_id,
                    order.expiry_date.isoformat() if order.expiry_date else "",
                    order.strike_price if order.strike_price is not None else "",
                    order.option_type or "",
                    order.side,
                    order.order_type,
                    order.lots,
                    order.quantity,
                    order.limit_price if order.limit_price is not None else "",
                    order.status,
                    order.filled_quantity,
                    order.average_fill_price if order.average_fill_price is not None else "",
                    charge.turnover if charge else "",
                    *[amounts.get(name, "") for name in component_names],
                    charge.total_charges if charge else "",
                    charge.rates_version if charge else "",
                    order.rejection_reason or "",
                ]
            )
        return buffer.getvalue()
