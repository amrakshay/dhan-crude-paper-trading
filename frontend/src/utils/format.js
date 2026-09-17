/** Formatting helpers. All prices are rupees; quantities are in barrels. */

const INR = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const INR0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

export function formatPrice(value, fallback = '—') {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return INR.format(value);
}

export function formatQty(value, fallback = '—') {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return INR0.format(value);
}

/** Compact form for OI and volume, which run to six or seven digits. */
export function formatCompact(value, fallback = '—') {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  const abs = Math.abs(value);
  if (abs >= 1e7) return `${(value / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${(value / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return INR0.format(value);
}

export function formatSignedPrice(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${INR.format(value)}`;
}

export function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

/** Tick age, the number that says whether the book can be trusted. */
export function formatAge(ms) {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

export function formatTime(msOrIso) {
  if (!msOrIso) return '—';
  const date = typeof msOrIso === 'number' ? new Date(msOrIso) : new Date(msOrIso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

/** Absolute and percentage change of LTP against the previous close. */
export function computeChange(row) {
  if (!row || row.ltp === null || row.ltp === undefined) return { absolute: null, percent: null };
  const reference = row.close ?? row.prevClose;
  if (!reference) return { absolute: null, percent: null };
  const absolute = row.ltp - reference;
  return { absolute, percent: (absolute / reference) * 100 };
}

/**
 * Countdown to a token expiry, e.g. "18h 42m 09s".
 *
 * Dhan access tokens last about 24 hours, so the difference between "expires
 * today" and "expires in 40 minutes" is the whole point — seconds are shown
 * once under an hour so a nearly-dead token is unmistakable.
 */
export function formatCountdown(seconds) {
  if (seconds === null || seconds === undefined) return null;
  if (seconds <= 0) return 'expired';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
  if (minutes > 0) return `${minutes}m ${String(secs).padStart(2, '0')}s`;
  return `${secs}s`;
}

/** Resident memory and similar byte counts, in the unit a human would pick. */
export function formatBytes(value, fallback = '—') {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[unit]}`;
}

/**
 * A duration in seconds as uptime reads best: days and hours once it is long,
 * seconds while it is still short. Distinct from `formatCountdown`, which
 * counts *down* to a deadline and therefore never shows days.
 */
export function formatDuration(seconds, fallback = '—') {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return fallback;
  const total = Math.floor(seconds);
  if (total < 60) return `${total}s`;
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m ${total % 60}s`;
}
