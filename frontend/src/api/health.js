import { api } from './client';

/**
 * System health. Admin-only on the server (`require_admin`), so a ROLE_USER
 * reaching these gets a 403 regardless of what the sidebar shows.
 */
export const healthApi = {
  /** One snapshot of the whole process. `problems` caps the log records returned. */
  get: (problems = 50, options) => api.get(`/healthcheck/system?problems=${problems}`, options),
  /** Recent WARNING+ records on their own, newest first. */
  problems: (limit = 50, options) => api.get(`/healthcheck/problems?limit=${limit}`, options),
};
