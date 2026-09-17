/**
 * Chart trading state: what the chart holds, and what a click would cost.
 *
 * Polls the API on the same 2 s cadence the Positions page uses, and for the
 * same reason (frontend/CLAUDE.md section 2): prices come from the socket, but
 * charges and realised P&L are computed on the server and cannot be derived in
 * the browser. Nothing here polls for tick data.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { chartTradingApi } from '../api/chartTrading';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';

const POLL_INTERVAL_MS = 2000;

export function useChartTrading(securityId, expiry) {
  const { activeId: portfolioId, active: portfolio, balance, refresh: refreshPortfolio } =
    useActivePortfolio();
  const [state, setState] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // A poll that lands mid-click would overwrite the fresher result.
  const busyRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!securityId) return;
    try {
      const [nextState, nextPreview] = await Promise.all([
        chartTradingApi.state(securityId, portfolioId),
        chartTradingApi.preview(securityId, expiry),
      ]);
      if (busyRef.current) return;
      setState(nextState);
      setPreview(nextPreview);
      setError(null);
    } catch (apiError) {
      setError(apiError.message);
    } finally {
      setLoading(false);
    }
  }, [securityId, expiry, portfolioId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    refresh();
    const timer = window.setInterval(() => {
      if (!cancelled) refresh();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refresh]);

  /** Run an action, keeping polls from clobbering its result. */
  const act = useCallback(
    async (operation) => {
      setBusy(true);
      busyRef.current = true;
      setError(null);
      try {
        const next = await operation();
        if (next) setState(next.state ?? next);
        return next;
      } catch (apiError) {
        setError(apiError.message);
        throw apiError;
      } finally {
        busyRef.current = false;
        setBusy(false);
        refresh();
      }
    },
    [refresh],
  );

  const click = useCallback(
    (side) =>
      act(async () => {
        const result = await chartTradingApi.click(
          securityId, side, expiry, portfolioId,
        );
        // One click moves money, and the HUD shows the balance beside the
        // cost, so the header figure must not lag behind the trade.
        refreshPortfolio?.();
        return result;
      }),
    [act, securityId, expiry, portfolioId, refreshPortfolio],
  );

  const setLevels = useCallback(
    (tradeId, body) => act(() => chartTradingApi.setLevels(tradeId, body)),
    [act],
  );

  const close = useCallback(
    (tradeId) => act(() => chartTradingApi.close(tradeId)),
    [act],
  );

  return {
    trade: state?.trade ?? null,
    underlyingPrice: state?.underlyingPrice ?? null,
    preview,
    // The money context the HUD puts on screen BEFORE the click, because
    // one-click entry has no confirm step to put it in.
    portfolioName: portfolio?.name ?? null,
    availableBalance: balance?.available ?? null,
    loading,
    busy,
    error,
    clearError: () => setError(null),
    refresh,
    click,
    setLevels,
    close,
  };
}
