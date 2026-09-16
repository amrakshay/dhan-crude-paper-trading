import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { ApiError, api } from '../api/client';

const AuthContext = createContext(null);

/**
 * Session state for the whole app.
 *
 * `/auth/me` returns the role, the pages that role may see and whether a
 * password change is still owed, so the first paint can route correctly
 * without a second round trip.
 */
export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setSession(await api.get('/auth/me'));
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        console.error('Session check failed', error);
      }
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (email, password) => {
    const result = await api.post('/auth/login', { email, password });
    setSession(result);
    return result;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout');
    } finally {
      setSession(null);
    }
  }, []);

  const value = useMemo(() => {
    const pages = session?.pages ?? [];
    return {
      session,
      loading,
      login,
      logout,
      refresh,
      role: session?.role ?? null,
      isAdmin: session?.role === 'ROLE_ACCOUNT_ADMIN',
      mustChangePassword: Boolean(session?.mustChangePassword),
      pages,
      // Advisory: the server enforces the same rule on every endpoint.
      canSee: (path) => pages.includes(path),
    };
  }, [session, loading, login, logout, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside an AuthProvider');
  return context;
}
