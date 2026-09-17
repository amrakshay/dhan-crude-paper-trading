import { api } from './client';

/**
 * Portfolios and their cash ledger.
 *
 * Every balance the server returns is four numbers — cash, blockedMargin,
 * available, equity — and `marginIsEstimate` is always true. Do not collapse
 * them into one figure in a component: a single "balance" is what hides what a
 * short position ties up.
 */
export const portfoliosApi = {
  list: (includeArchived = false) =>
    api.get(`/portfolios?includeArchived=${includeArchived}`),
  get: (id) => api.get(`/portfolios/${id}`),
  ledger: (id, params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return;
      query.append(key, value);
    });
    const suffix = query.toString();
    return api.get(`/portfolios/${id}/ledger${suffix ? `?${suffix}` : ''}`);
  },

  // Admin only. The server enforces this independently of the UI.
  create: (payload) => api.post('/portfolios', payload),
  update: (id, payload) => api.put(`/portfolios/${id}`, payload),
  deposit: (id, payload) => api.post(`/portfolios/${id}/deposit`, payload),
  withdraw: (id, payload) => api.post(`/portfolios/${id}/withdraw`, payload),
  archive: (id) => api.post(`/portfolios/${id}/archive`),
  restore: (id) => api.post(`/portfolios/${id}/restore`),
  // Refused by the server for any portfolio with history, whatever the UI
  // offers. Archive is the answer for anything that has ever traded.
  remove: (id) => api.delete(`/portfolios/${id}`),
};
