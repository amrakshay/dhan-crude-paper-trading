import { api } from './client';

/** The NSE Options Trader page. Single orders go through ordersApi. */
export const optionsTraderApi = {
  underlyings: () => api.get('/options-trader/underlyings'),
  chain: (underlying, expiry) => {
    const query = new URLSearchParams({ underlying });
    if (expiry) query.append('expiry', expiry);
    return api.get(`/options-trader/chain?${query.toString()}`);
  },
  // `keepalive` so a reload or a closed tab still sends it.
  release: () => api.delete('/options-trader/view', { keepalive: true }),
  previewBasket: (payload) => api.post('/options-trader/basket/preview', payload),
  placeBasket: (payload) => api.post('/options-trader/basket', payload),
  exitGroup: (payload) => api.post('/options-trader/exit-group', payload),
  positions: (portfolioId) =>
    api.get(`/options-trader/positions?portfolioId=${encodeURIComponent(portfolioId)}`),
  span: () => api.get('/options-trader/span'),
  riskDesk: (portfolioId) =>
    api.get(`/options-trader/rms?portfolioId=${encodeURIComponent(portfolioId)}`),
};
