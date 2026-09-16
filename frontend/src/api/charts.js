import { api } from './client';

/**
 * Candle history for the price chart.
 *
 * History is loaded once per (instrument, timeframe) and then left alone: the
 * newest bar is updated from the shared WebSocket, not by polling this.
 */
export const chartsApi = {
  timeframes: () => api.get('/market/timeframes'),
  candles: (securityId, timeframe) =>
    api.get(
      `/market/candles?securityId=${encodeURIComponent(securityId)}` +
        `&timeframe=${encodeURIComponent(timeframe)}`,
    ),
};
