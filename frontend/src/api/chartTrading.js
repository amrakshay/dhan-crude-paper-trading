import { api } from './client';

/**
 * One-click trading from the futures chart.
 *
 * PAPER RUPEES ONLY, and a Sell buys a put -- it never writes one. The levels
 * sent to `setLevels` are prices of the UNDERLYING FUTURE, not of the option.
 */
export const chartTradingApi = {
  state: (securityId) =>
    api.get(`/chart-trading/state?securityId=${encodeURIComponent(securityId)}`),

  preview: (securityId, expiry) =>
    api.get(
      `/chart-trading/preview?securityId=${encodeURIComponent(securityId)}` +
        (expiry ? `&expiry=${encodeURIComponent(expiry)}` : ''),
    ),

  click: (securityId, side, expiry) =>
    api.post('/chart-trading/click', { securityId, side, expiry: expiry ?? null }),

  setLevels: (tradeId, body) =>
    api.patch(`/chart-trading/trades/${tradeId}/levels`, body),

  close: (tradeId) => api.post(`/chart-trading/trades/${tradeId}/close`),
};
