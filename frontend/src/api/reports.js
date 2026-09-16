import { api } from './client';

function toQuery(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    query.append(key, value);
  });
  const suffix = query.toString();
  return suffix ? `?${suffix}` : '';
}

export const reportsApi = {
  pnl: (params) => api.get(`/reports/pnl${toQuery(params)}`),
  // Downloads go through the browser so the Content-Disposition filename is
  // honoured; the session cookie rides along automatically.
  pnlCsvUrl: (params) => `/api/reports/pnl/export.csv${toQuery(params)}`,
  ordersCsvUrl: (params) => `/api/reports/orders/export.csv${toQuery(params)}`,
};

export const notesApi = {
  list: (params) => api.get(`/notes${toQuery(params)}`),
  create: (payload) => api.post('/notes', payload),
  update: (id, payload) => api.put(`/notes/${id}`, payload),
  remove: (id) => api.delete(`/notes/${id}`),
};
