import { api } from './client';

export const ordersApi = {
  preview: (payload) => api.post('/orders/preview', payload),
  place: (payload) => api.post('/orders', payload),
  list: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return;
      if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
      else query.append(key, value);
    });
    const suffix = query.toString();
    return api.get(`/orders${suffix ? `?${suffix}` : ''}`);
  },
  get: (id) => api.get(`/orders/${id}`),
  cancel: (id) => api.post(`/orders/${id}/cancel`),
};

export const positionsApi = {
  list: (includeClosed = false, portfolioId = null) =>
    api.get(
      `/positions?includeClosed=${includeClosed}` +
        (portfolioId ? `&portfolioId=${encodeURIComponent(portfolioId)}` : ''),
    ),
  close: (id, payload) => api.post(`/positions/${id}/close`, payload),
};

export const chargesApi = {
  rates: () => api.get('/charges/rates'),
  estimate: (payload) => api.post('/charges/estimate', payload),
};
