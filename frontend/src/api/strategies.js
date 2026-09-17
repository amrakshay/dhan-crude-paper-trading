import { api } from './client';

/**
 * Strategy modules and the generic capabilities they can use.
 *
 * Reading is open to any signed-in user — Positions and Order History need the
 * labels to mark a row as belonging to a disabled strategy. Toggling is
 * admin-only and the server enforces that independently of this client.
 */
export const strategiesApi = {
  list: () => api.get('/strategies'),
  // What switching a strategy off would cost, asked BEFORE it is switched off.
  disableWarnings: (key) => api.get(`/strategies/${key}/disable-warnings`),
  setStrategyEnabled: (key, enabled) =>
    api.put(`/strategies/${key}/enabled`, { enabled }),
  setCapabilityEnabled: (key, enabled) =>
    api.put(`/strategies/capabilities/${key}/enabled`, { enabled }),
};
