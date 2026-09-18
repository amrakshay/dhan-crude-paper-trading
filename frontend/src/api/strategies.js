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

  // ARMING is a separate switch from enabling, and it only exists for a module
  // that trades on a schedule. Enabled means it computes, decides and writes a
  // decision record; armed means it may submit an order with nobody watching.
  armWarnings: (key) => api.get(`/strategies/${key}/arm-warnings`),
  setStrategyArmed: (key, armed) => api.put(`/strategies/${key}/armed`, { armed }),

  // WHETHER ONE OF A STRATEGY'S OWN RULES IS ENFORCED — not what the rule is.
  // The lookbacks, thresholds and multiples live in the strategy's YAML and
  // are editable from nowhere (root CLAUDE.md §3a); these switches say whether
  // the application obeys them, which is runtime state like enabled and armed.
  policyWarnings: (key, policy, enforced) =>
    api.get(
      `/strategies/${key}/policy-warnings/${policy}?enforced=${enforced ? 'true' : 'false'}`,
    ),
  setStrategyPolicy: (key, policy, enforced) =>
    api.put(`/strategies/${key}/policies/${policy}`, { enforced }),

  // Runtime VALUES rather than booleans — the two clock times the scheduler
  // runs on. Same line as the policies: WHEN the machine wakes up is editable,
  // what the rule says is not. A null value clears the override and goes back
  // to the strategy's own configured time.
  settingWarnings: (key, setting, value) =>
    api.get(
      `/strategies/${key}/setting-warnings/${setting}?value=${encodeURIComponent(value)}`,
    ),
  setStrategySetting: (key, setting, value) =>
    api.put(`/strategies/${key}/settings/${setting}`, { value }),
};
