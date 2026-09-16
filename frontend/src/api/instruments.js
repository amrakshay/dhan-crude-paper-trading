import { api } from './client';

export const instrumentsApi = {
  status: () => api.get('/instruments/status'),
  expiries: () => api.get('/instruments/expiries'),
  nearFuture: () => api.get('/instruments/futures/near'),
  chain: (expiry) => api.get(`/instruments/chain${expiry ? `?expiry=${expiry}` : ''}`),
  refresh: (force = false) => api.post(`/instruments/refresh?force=${force}`),
};

export const marketApi = {
  status: () => api.get('/market/status'),
  snapshot: (securityIds) =>
    api.get(`/market/snapshot${securityIds ? `?securityIds=${securityIds.join(',')}` : ''}`),
  resync: () => api.post('/market/resync'),
};
