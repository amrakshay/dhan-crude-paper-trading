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
};
