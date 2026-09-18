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

/**
 * The NSE Swing Momentum rotation: what it decided, what it holds, and how it
 * has done.
 *
 * Reading is open to any signed-in user — the decision journal is a record,
 * and a record only an admin may read is not much of one. TRIGGERING a run is
 * admin-only and the server enforces that independently of this client.
 *
 * Every figure that could not be measured comes back as `null`, never 0. A
 * breadth of null means "we could not measure breadth", which is a different
 * answer from "breadth says buy nothing", and only one of them is a reason to
 * hold cash.
 */
export const swingApi = {
  strategies: () => api.get('/swing/strategies'),
  status: (strategyKey, portfolioId) =>
    api.get(`/swing/status${toQuery({ strategyKey, portfolioId })}`),
  book: (strategyKey, portfolioId) =>
    api.get(`/swing/book${toQuery({ strategyKey, portfolioId })}`),
  history: (strategyKey, limit = 30, runKind) =>
    api.get(`/swing/history${toQuery({ strategyKey, limit, runKind })}`),
  session: (sessionId, strategyKey) =>
    api.get(`/swing/sessions/${sessionId}${toQuery({ strategyKey })}`),
  stops: (strategyKey, portfolioId, limit = 100) =>
    api.get(`/swing/stops${toQuery({ strategyKey, portfolioId, limit })}`),
  performance: (strategyKey, portfolioId) =>
    api.get(`/swing/performance${toQuery({ strategyKey, portfolioId })}`),

  // The rule as CONFIGURED — every threshold, multiple and lookback read out
  // of the strategy's own YAML. The "How it works" page renders this rather
  // than restating the numbers in JavaScript, which would be a second source
  // of truth that goes stale silently.
  explain: (strategyKey) => api.get(`/swing/explain${toQuery({ strategyKey })}`),

  // Both obey the same gates as the scheduled runs. The nightly never places
  // an order at all; the rebalance places one only when the strategy is ARMED.
  runNightly: (strategyKey, portfolioId, force = false) =>
    api.post(`/swing/runs/nightly${toQuery({ strategyKey })}`, { portfolioId, force }),
  runRebalance: (strategyKey, portfolioId, force = false) =>
    api.post(`/swing/runs/rebalance${toQuery({ strategyKey })}`, { portfolioId, force }),
};
