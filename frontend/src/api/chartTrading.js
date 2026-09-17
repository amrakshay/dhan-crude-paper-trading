import { api } from './client';

/**
 * One-click trading from the futures chart.
 *
 * PAPER RUPEES ONLY, and a Sell buys a put -- it never writes one. The levels
 * sent to `setLevels` are prices of the UNDERLYING FUTURE, not of the option.
 *
 * `portfolioId` is sent explicitly on state and click: the same strategy can
 * run in two portfolios with two independent chart trades on the same future,
 * and a click in one must not close the other.
 */
export const chartTradingApi = {
  state: (securityId, portfolioId) =>
    api.get(
      `/chart-trading/state?securityId=${encodeURIComponent(securityId)}` +
        (portfolioId ? `&portfolioId=${encodeURIComponent(portfolioId)}` : ''),
    ),

  preview: (securityId, expiry) =>
    api.get(
      `/chart-trading/preview?securityId=${encodeURIComponent(securityId)}` +
        (expiry ? `&expiry=${encodeURIComponent(expiry)}` : ''),
    ),

  click: (securityId, side, expiry, portfolioId) =>
    api.post('/chart-trading/click', {
      securityId,
      side,
      expiry: expiry ?? null,
      portfolioId: portfolioId ?? undefined,
    }),

  setLevels: (tradeId, body) =>
    api.patch(`/chart-trading/trades/${tradeId}/levels`, body),

  close: (tradeId) => api.post(`/chart-trading/trades/${tradeId}/close`),
};
