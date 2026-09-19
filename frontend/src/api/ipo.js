import { api } from './client';

/**
 * The IPO dashboard.
 *
 * Reading the three tabs is open to any signed-in user. The three ACTIONS --
 * Applied, Accepted UPI Mandate, Reject -- are admin-only, and the server
 * enforces that with `require_admin` independently of this client. The page
 * hides the buttons for a ROLE_USER, which is presentation, not access
 * control.
 *
 * Every figure that could not be measured comes back as `null`, never 0. A
 * `gmp` of null means the source prints "--" for that IPO, which is a real
 * answer and not a premium of zero.
 */
export const ipoApi = {
  closingToday: () => api.get('/ipo/closing-today'),
  closingNext: () => api.get('/ipo/closing-next'),
  listed: () => api.get('/ipo/listed'),
  status: () => api.get('/ipo/status'),

  // Admin only. `value` rather than a verb because all three toggle: a mis-tap
  // has to be undoable, and Reject has to be reversible before the IPO closes.
  setAction: (ipoId, action, value) =>
    api.post(`/ipo/${ipoId}/action`, { action, value }),

  // Admin only. Fetches the board now; does NOT consume a scheduled slot, so
  // pressing it at 13:55 cannot be what stops the 14:00 reminder.
  refresh: () => api.post('/ipo/refresh', {}),
};

/** The source page for one IPO, for the link in the name column.
 *
 * Built HERE rather than on the server: `webnodejs.investorgain.com` is the
 * host the backend CALLS and is named in exactly one module there
 * (`tests/test_outbound_hosts.py`); this is the human page on `www.`, which
 * the backend never fetches. Keeping it out of Python keeps that list honest.
 */
export const IPO_SOURCE_BASE = 'https://www.investorgain.com';
export const IPO_SOURCE_PAGE = `${IPO_SOURCE_BASE}/report/ipo-gmp-live/331/`;

export function ipoSourceUrl(sourcePath) {
  if (!sourcePath) return IPO_SOURCE_PAGE;
  return `${IPO_SOURCE_BASE}${sourcePath}`;
}
