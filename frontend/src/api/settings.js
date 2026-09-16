import { api } from './client';

export const settingsApi = {
  get: () => api.get('/settings'),
  save: (payload) => api.put('/settings', payload),
  validate: (payload) => api.post('/settings/validate', payload),
};
