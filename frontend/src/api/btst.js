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
 * NSE BTST Overnight: what it holds overnight, what today's scan has found so
 * far, and how the overnight gaps have actually come out.
 *
 * Reading is open to any signed-in user — the decision journal is a record.
 * TRIGGERING a run is admin-only and the server enforces that independently of
 * this client.
 *
 * Every figure that could not be measured comes back as `null`, never 0. An
 * overnight gap of null means the position has not been sold yet, which is a
 * different answer from a gap of 0.00%.
 */
export const btstApi = {
  status: (strategyKey, portfolioId) =>
    api.get(`/btst/status${toQuery({ strategyKey, portfolioId })}`),

  // TODAY'S SCAN, live. The tab the rotation has no equivalent of: its
  // decision happens overnight in one shot, and this one forms over the
  // afternoon. It computes the identical funnel the scheduled scan computes,
  // journals nothing and places nothing.
  signals: (strategyKey, portfolioId) =>
    api.get(`/btst/signals${toQuery({ strategyKey, portfolioId })}`),

  history: (strategyKey, limit = 30) =>
    api.get(`/btst/history${toQuery({ strategyKey, limit })}`),
  performance: (strategyKey) =>
    api.get(`/btst/performance${toQuery({ strategyKey })}`),
  configuration: (strategyKey) =>
    api.get(`/btst/configuration${toQuery({ strategyKey })}`),

  // The rule as CONFIGURED — every threshold and lookback read out of the
  // strategy's own YAML. The "How it works" tab renders this rather than
  // restating the numbers in JavaScript, which would be a second source of
  // truth that goes stale silently.
  explain: (strategyKey) => api.get(`/btst/explain${toQuery({ strategyKey })}`),

  // IS IT HEALTHY, and DID THE EXIT RUN. ADMIN-ONLY, unlike every other read
  // here: the rest of the page is a journal and a journal is history, but this
  // reports live machinery state and recent log records. The server refuses a
  // ROLE_USER with a 403 regardless of whether the tab is shown.
  health: (strategyKey, portfolioId) =>
    api.get(`/btst/health${toQuery({ strategyKey, portfolioId })}`),

  // Both obey the same gates as the scheduled runs. `placeOrders: false`
  // computes and journals the identical decision and places nothing, which is
  // exactly what an unarmed scheduled run does.
  runScan: (strategyKey, portfolioId, { force = false, placeOrders = true } = {}) =>
    api.post('/btst/runs/scan', { strategyKey, portfolioId, force, placeOrders }),
  runExit: (strategyKey, portfolioId) =>
    api.post('/btst/runs/exit', { strategyKey, portfolioId }),
};
