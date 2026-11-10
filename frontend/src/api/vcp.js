import { api } from './client';

/**
 * NSE VCP Swing: today's candidates, the open positions with their stops,
 * every decision, and the run journal -- one read-only payload.
 *
 * Every figure that could not be measured arrives as `null`, never 0.
 */
export const vcpApi = {
  overview: (portfolioId) =>
    api.get(`/vcp/overview${portfolioId ? `?portfolioId=${portfolioId}` : ''}`),
};
