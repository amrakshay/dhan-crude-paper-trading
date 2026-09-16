/**
 * User management and profile calls.
 *
 * The role -> pages mapping comes from the server (conf/role-pages.json) so
 * the sidebar and the client-side routes are driven by the same file the
 * backend reads. It is advisory: the API enforces the same rules itself, and
 * hiding a nav item stops nobody from calling an endpoint directly.
 */
import { api } from './client';

export const usersApi = {
  list: () => api.get('/users'),
  get: (id) => api.get(`/users/${id}`),
  create: (payload) => api.post('/users', payload),
  update: (id, payload) => api.put(`/users/${id}`, payload),
  remove: (id) => api.delete(`/users/${id}`),

  me: () => api.get('/users/me'),
  updateProfile: (payload) => api.put('/users/me', payload),
  changePassword: (currentPassword, newPassword) =>
    api.post('/users/me/password', { currentPassword, newPassword }),

  rolePages: () => api.get('/users/role-pages'),
};

export const ROLE_LABELS = {
  ROLE_ACCOUNT_ADMIN: 'Account admin',
  ROLE_USER: 'User',
};

export const STATUS_LABELS = {
  ACTIVE: 'Active',
  INACTIVE: 'Inactive',
};
