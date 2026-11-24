import { api } from './client';

/** The NSE Options Trader page. Orders go through ordersApi with `product`. */
export const optionsTraderApi = {
  underlyings: () => api.get('/options-trader/underlyings'),
  chain: (underlying, expiry) => {
    const query = new URLSearchParams({ underlying });
    if (expiry) query.append('expiry', expiry);
    return api.get(`/options-trader/chain?${query.toString()}`);
  },
  // The page closed: release the strikes it pinned to the feed now, rather
  // than waiting for the server's TTL.
  // `keepalive` so a reload or a closed tab still sends it.
  release: () => api.delete('/options-trader/view', { keepalive: true }),
};
