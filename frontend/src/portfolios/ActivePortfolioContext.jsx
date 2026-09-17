import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { portfoliosApi } from '../api/portfolios';

/**
 * Which portfolio the portal is pointing at.
 *
 * This lives BESIDE MarketFeedContext, never inside it. The feed context has a
 * performance contract — rows in a ref, one version bump per 100 ms batch — and
 * putting portfolio state in it would re-render every subscriber whenever the
 * picker moved or a balance ticked (frontend/CLAUDE.md section 2).
 *
 * The selection is per browser, remembered in localStorage, and always sent
 * EXPLICITLY as `portfolioId` on the requests that need it. The server never
 * infers it from a session: a trade landing in the wrong book is precisely what
 * this picker exists to prevent, and an inferred default is how that happens.
 */
const ActivePortfolioContext = createContext(null);

const STORAGE_KEY = 'dcpt.activePortfolioId';

function readStored() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? Number(raw) : null;
  } catch {
    // Private windows and blocked site data both throw here. A forgotten
    // selection is a minor inconvenience; a crash is not acceptable.
    return null;
  }
}

function writeStored(id) {
  try {
    if (id === null || id === undefined) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    /* see readStored */
  }
}

export function ActivePortfolioProvider({ children }) {
  const [portfolios, setPortfolios] = useState([]);
  const [activeId, setActiveId] = useState(readStored);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const response = await portfoliosApi.list();
      const rows = response.portfolios ?? [];
      setPortfolios(rows);
      setError(null);

      // A remembered id that has been archived or deleted must not stick: the
      // header would name a portfolio that no longer trades.
      setActiveId((current) => {
        const stillThere = rows.some((row) => row.id === current);
        if (stillThere) return current;
        const next = rows.length > 0 ? rows[0].id : null;
        writeStored(next);
        return next;
      });
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const select = useCallback((id) => {
    const next = id === null || id === undefined ? null : Number(id);
    writeStored(next);
    setActiveId(next);
  }, []);

  const value = useMemo(() => {
    const active = portfolios.find((row) => row.id === activeId) ?? null;
    return {
      portfolios,
      activeId,
      active,
      balance: active?.balance ?? null,
      loading,
      error,
      select,
      // Called after anything that moves money, so the header figure does not
      // go stale behind a trade.
      refresh: load,
    };
  }, [portfolios, activeId, loading, error, select, load]);

  return (
    <ActivePortfolioContext.Provider value={value}>
      {children}
    </ActivePortfolioContext.Provider>
  );
}

export function useActivePortfolio() {
  const context = useContext(ActivePortfolioContext);
  if (!context) {
    throw new Error('useActivePortfolio must be used inside ActivePortfolioProvider');
  }
  return context;
}
