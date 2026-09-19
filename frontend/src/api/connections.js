import { api } from './client';

/**
 * The Connections page's API.
 *
 * Every one of these is admin-only on the server. The sidebar hiding
 * `/connections` from a ROLE_USER is presentation; `require_admin` on the
 * routes is what refuses (frontend/CLAUDE.md §5).
 *
 * `save` sends only what changed. An OMITTED secret means "keep the stored
 * one" -- the same rule the Settings page's access token has always followed,
 * so the browser never has to round-trip a token to change another field.
 */
export const connectionsApi = {
  list: () => api.get('/connections'),
  get: (provider) => api.get(`/connections/${provider}`),
  save: (provider, payload) => api.put(`/connections/${provider}`, payload),
  validate: (provider, settings = {}) =>
    api.post(`/connections/${provider}/validate`, { settings }),
  sendTestMessage: () => api.post('/connections/telegram/test-message', {}),
  listen: (seconds = 60) => api.post('/connections/telegram/listen', { seconds }),
  alerts: (limit = 50) => api.get(`/connections/alerts?limit=${limit}`),

  /**
   * Every alert rule this build has, with when each last fired.
   *
   * `strategyKey` narrows it to the rules ABOUT that strategy, which is what a
   * strategy's own page shows. Omit it for the system view, which shows all.
   */
  // Narrow by strategy OR by category, never both -- the server returns a 400
  // for both at once, because they are two different questions and answering
  // them together would quietly return nothing. `category` exists for a page
  // that is not a strategy page: the IPO rules are deliberately not
  // strategy-scoped, so `strategyKey` cannot reach them.
  catalogue: (strategyKey, category) => {
    const query = new URLSearchParams();
    if (strategyKey) query.set('strategyKey', strategyKey);
    if (category) query.set('category', category);
    const suffix = query.toString();
    return api.get(`/connections/alerts/catalogue${suffix ? `?${suffix}` : ''}`);
  },
};
