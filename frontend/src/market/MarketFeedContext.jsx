/**
 * One WebSocket for the whole app.
 *
 * Mirrors the backend's design: the server keeps a single upstream Dhan
 * connection and fans out, and the browser keeps a single socket to the server
 * and fans out to components. Opening a socket per page would multiply
 * snapshots for no benefit.
 *
 * Rows live in a ref (a mutable Map) rather than in state, so an incoming batch
 * costs a Map.set per changed row instead of rebuilding an object graph. A
 * single counter bump then re-renders subscribers. The server already coalesces
 * to one message per 100ms, so this is ~10 renders/sec regardless of tick rate.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

const MarketFeedContext = createContext(null);

const RECONNECT_INITIAL_MS = 500;
const RECONNECT_MAX_MS = 15000;

export function MarketFeedProvider({ children }) {
  const rowsRef = useRef(new Map());
  const socketRef = useRef(null);
  const reconnectRef = useRef(RECONNECT_INITIAL_MS);
  const reconnectTimerRef = useRef(null);
  const closedByUsRef = useRef(false);

  const [version, setVersion] = useState(0);
  const [status, setStatus] = useState(null);
  const [connectionState, setConnectionState] = useState('CONNECTING');
  const [lastMessageAt, setLastMessageAt] = useState(null);

  const applyRows = useCallback((rows) => {
    if (!rows || rows.length === 0) return;
    const map = rowsRef.current;
    for (const row of rows) {
      const existing = map.get(row.securityId);
      // Merge rather than replace: a lite payload (no depth) must not erase
      // depth an earlier full payload delivered.
      map.set(row.securityId, existing ? { ...existing, ...row } : row);
    }
  }, []);

  const connect = useCallback(() => {
    if (socketRef.current) return;
    closedByUsRef.current = false;
    setConnectionState('CONNECTING');

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws/market`);
    socketRef.current = socket;

    socket.onopen = () => {
      reconnectRef.current = RECONNECT_INITIAL_MS;
      setConnectionState('CONNECTED');
    };

    socket.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }

      if (payload.type === 'snapshot') {
        rowsRef.current = new Map();
        applyRows(payload.rows);
      } else if (payload.type === 'update') {
        applyRows(payload.rows);
      }

      if (payload.status) setStatus(payload.status);
      setLastMessageAt(Date.now());
      setVersion((value) => value + 1);
    };

    socket.onerror = () => setConnectionState('ERROR');

    socket.onclose = () => {
      socketRef.current = null;
      if (closedByUsRef.current) return;
      setConnectionState('RECONNECTING');
      const delay = reconnectRef.current;
      reconnectRef.current = Math.min(delay * 2, RECONNECT_MAX_MS);
      reconnectTimerRef.current = window.setTimeout(connect, delay);
    };
  }, [applyRows]);

  useEffect(() => {
    connect();
    return () => {
      closedByUsRef.current = true;
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect]);

  const send = useCallback((message) => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
    }
  }, []);

  const requestSnapshot = useCallback(() => send({ action: 'snapshot' }), [send]);

  const getRow = useCallback((securityId) => {
    if (!securityId) return null;
    return rowsRef.current.get(String(securityId)) ?? null;
  }, []);

  const getRows = useCallback(() => rowsRef.current, []);

  const value = useMemo(
    () => ({
      version,
      status,
      connectionState,
      lastMessageAt,
      getRow,
      getRows,
      send,
      requestSnapshot,
    }),
    [version, status, connectionState, lastMessageAt, getRow, getRows, send, requestSnapshot],
  );

  return <MarketFeedContext.Provider value={value}>{children}</MarketFeedContext.Provider>;
}

export function useMarketFeed() {
  const context = useContext(MarketFeedContext);
  if (!context) throw new Error('useMarketFeed must be used inside a MarketFeedProvider');
  return context;
}

/** One instrument's live row. Re-renders with the feed. */
export function useMarketRow(securityId) {
  const { getRow, version } = useMarketFeed();
  return useMemo(() => getRow(securityId), [getRow, securityId, version]);
}

/**
 * Feed health, derived once so every consumer agrees.
 *
 * `state` folds the browser socket and the upstream feed into one value,
 * because a live browser socket carrying a dead upstream must not look healthy.
 */
export function useFeedHealth() {
  const { status, connectionState, lastMessageAt } = useMarketFeed();
  const [, setNow] = useState(Date.now());

  // The age must keep counting up while the feed is silent -- that is exactly
  // when the number matters.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);

  const upstream = status?.feed?.state ?? null;
  const synthetic = Boolean(status?.synthetic);
  const lastTickAgeMs = status?.book?.lastTickAgeMs ?? null;

  let state = connectionState;
  if (connectionState === 'CONNECTED') {
    state = upstream ?? 'CONNECTED';
  }

  const socketAgeMs = lastMessageAt ? Date.now() - lastMessageAt : null;
  const stale = socketAgeMs !== null && socketAgeMs > 5000;

  return {
    state,
    synthetic,
    stale,
    lastTickAgeMs,
    socketAgeMs,
    upstream,
    marketOpen: status?.market?.isOpen ?? null,
    detail: status?.feed?.detail ?? null,
    subscribed: status?.feed?.subscribed ?? null,
    error: status?.error ?? null,
  };
}
