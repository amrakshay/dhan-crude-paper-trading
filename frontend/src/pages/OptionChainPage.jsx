import { forwardRef, memo, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Card,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { instrumentsApi } from '../api/instruments';
import { useFeedHealth, useMarketFeed, useMarketRow } from '../market/MarketFeedContext';
import SyntheticBanner from '../components/SyntheticBanner';
import OrderTicket from '../components/OrderTicket';
import { formatCompact, formatPrice, formatQty } from '../utils/format';

function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
}

/**
 * One strike: calls on the left, puts on the right, strike in the middle --
 * the conventional Indian broker layout.
 *
 * Memoised on the row object identities. The feed context replaces a row object
 * only when that instrument actually changed, so an 80-strike chain re-renders
 * just the handful of rows that ticked rather than all of them.
 */
const ChainRow = memo(
  forwardRef(function ChainRow(
    { strike, call, put, isAtm, underlying, showAllGreeks, onSelect },
    ref,
  ) {
  const theme = useTheme();
  const callItm = underlying !== null && strike < underlying;
  const putItm = underlying !== null && strike > underlying;

  const itmSx = { bgcolor: theme.market.itm };
  const cell = { whiteSpace: 'nowrap' };
  const clickable = {
    cursor: 'pointer',
    '&:hover': { textDecoration: 'underline' },
  };

  const oiChangeColor = (value) =>
    value === null || value === undefined
      ? 'text.primary'
      : value >= 0
        ? theme.market.up
        : theme.market.down;

  return (
    <TableRow
      ref={ref}
      className="numeric"
      sx={isAtm ? { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: '-2px' } : undefined}
    >
      {/* ---- CALLS ---- */}
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}) }}>
        {formatCompact(call?.oi)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}), color: oiChangeColor(call?.oiChange) }}>
        {call?.oiChange === null || call?.oiChange === undefined ? '—' : formatCompact(call.oiChange)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}) }}>
        {formatCompact(call?.volume)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}) }}>
        {num(call?.iv, 1)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}) }}>
        {num(call?.delta, 3)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}) }}>
        {num(call?.theta, 2)}
      </TableCell>
      {showAllGreeks ? (
        <>
          <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}) }}>
            {num(call?.gamma, 5)}
          </TableCell>
          <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}) }}>
            {num(call?.vega, 3)}
          </TableCell>
        </>
      ) : null}
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}), color: theme.market.up }}>
        {formatPrice(call?.bid)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(callItm ? itmSx : {}), color: theme.market.down }}>
        {formatPrice(call?.ask)}
      </TableCell>
      <Tooltip title={call ? `Trade ${call.tradingSymbol}` : ''} placement="left">
        <TableCell
          align="right"
          onClick={() => call && onSelect?.(call)}
          sx={{ ...cell, ...(callItm ? itmSx : {}), fontWeight: 600, ...(call ? clickable : {}) }}
        >
          {formatPrice(call?.ltp)}
        </TableCell>
      </Tooltip>

      {/* ---- STRIKE ---- */}
      <TableCell
        align="center"
        sx={{
          ...cell,
          fontWeight: 700,
          bgcolor: isAtm ? theme.market.atm : theme.palette.action.hover,
          borderLeft: `1px solid ${theme.palette.divider}`,
          borderRight: `1px solid ${theme.palette.divider}`,
          position: 'sticky',
        }}
      >
        {formatQty(strike)}
      </TableCell>

      {/* ---- PUTS ---- */}
      <Tooltip title={put ? `Trade ${put.tradingSymbol}` : ''} placement="right">
        <TableCell
          align="right"
          onClick={() => put && onSelect?.(put)}
          sx={{ ...cell, ...(putItm ? itmSx : {}), fontWeight: 600, ...(put ? clickable : {}) }}
        >
          {formatPrice(put?.ltp)}
        </TableCell>
      </Tooltip>
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}), color: theme.market.up }}>
        {formatPrice(put?.bid)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}), color: theme.market.down }}>
        {formatPrice(put?.ask)}
      </TableCell>
      {showAllGreeks ? (
        <>
          <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}) }}>
            {num(put?.vega, 3)}
          </TableCell>
          <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}) }}>
            {num(put?.gamma, 5)}
          </TableCell>
        </>
      ) : null}
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}) }}>
        {num(put?.theta, 2)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}) }}>
        {num(put?.delta, 3)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}) }}>
        {num(put?.iv, 1)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}) }}>
        {formatCompact(put?.volume)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}), color: oiChangeColor(put?.oiChange) }}>
        {put?.oiChange === null || put?.oiChange === undefined ? '—' : formatCompact(put.oiChange)}
      </TableCell>
      <TableCell align="right" sx={{ ...cell, ...(putItm ? itmSx : {}) }}>
        {formatCompact(put?.oi)}
      </TableCell>
    </TableRow>
  );
  }),
);

export default function OptionChainPage() {
  const [ticketContract, setTicketContract] = useState(null);
  const { getRows, version } = useMarketFeed();
  const { synthetic } = useFeedHealth();

  const [expiries, setExpiries] = useState([]);
  const [expiry, setExpiry] = useState('');
  const [chain, setChain] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAllGreeks, setShowAllGreeks] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const atmRowRef = useRef(null);
  const hasScrolledRef = useRef(false);

  const underlyingRow = useMarketRow(chain?.underlyingFuture?.securityId);
  const underlying = underlyingRow?.ltp ?? null;

  useEffect(() => {
    (async () => {
      try {
        const result = await instrumentsApi.expiries();
        setExpiries(result.optionExpiries);
        if (result.optionExpiries.length > 0) setExpiry(result.optionExpiries[0]);
      } catch (loadError) {
        setError(loadError.message);
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!expiry) return;
    setLoading(true);
    hasScrolledRef.current = false;
    (async () => {
      try {
        setChain(await instrumentsApi.chain(expiry));
        setError(null);
      } catch (loadError) {
        setError(loadError.message);
        setChain(null);
      } finally {
        setLoading(false);
      }
    })();
  }, [expiry]);

  /**
   * Join the static ladder (from the DB) to the live book (from the feed).
   * Recomputed on every feed version bump, but each row object only changes
   * identity when that instrument ticked, so memoised rows stay put.
   */
  const rows = useMemo(() => {
    if (!chain) return [];
    const book = getRows();
    return chain.rows.map((row) => ({
      strike: Number(row.strikePrice),
      call: row.call ? book.get(row.call.securityId) ?? null : null,
      put: row.put ? book.get(row.put.securityId) ?? null : null,
      callMeta: row.call,
      putMeta: row.put,
    }));
  }, [chain, getRows, version]);

  /** The strike closest to the underlying. Ties go to the lower strike, as on
   *  the backend, so the highlight never disagrees with the subscription. */
  const atmStrike = useMemo(() => {
    if (underlying === null || rows.length === 0) return null;
    let best = null;
    let bestDistance = Infinity;
    for (const row of rows) {
      const distance = Math.abs(row.strike - underlying);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = row.strike;
      }
    }
    return best;
  }, [rows, underlying]);

  // Scroll the ATM row into view once per expiry, then leave the user alone --
  // yanking the viewport back on every tick would make the chain unusable.
  useEffect(() => {
    if (!autoScroll || hasScrolledRef.current || !atmRowRef.current) return;
    atmRowRef.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
    hasScrolledRef.current = true;
  }, [atmStrike, autoScroll]);

  const subscribedCount = rows.filter((row) => row.call || row.put).length;

  return (
    <Stack spacing={3} sx={{ height: '100%' }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h2">Option Chain</Typography>
          <Typography variant="body2" color="text.secondary">
            CRUDEOIL options on the {chain?.underlyingFuture?.displayName ?? 'near future'}
            {underlying !== null ? ` · underlying ${formatPrice(underlying)}` : ''}
          </Typography>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center">
          <FormControlLabel
            control={<Switch size="small" checked={showAllGreeks} onChange={(e) => setShowAllGreeks(e.target.checked)} />}
            label={<Typography variant="body2">Gamma &amp; vega</Typography>}
          />
          <FormControlLabel
            control={<Switch size="small" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />}
            label={<Typography variant="body2">Auto-scroll to ATM</Typography>}
          />
          <FormControl size="small" sx={{ minWidth: 180 }}>
            <InputLabel id="expiry-label">Expiry</InputLabel>
            <Select
              labelId="expiry-label"
              label="Expiry"
              value={expiry}
              onChange={(event) => setExpiry(event.target.value)}
            >
              {expiries.map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Stack>
      </Stack>

      <SyntheticBanner />

      {error ? <Alert severity="error">{error}</Alert> : null}

      {chain ? (
        <Stack direction="row" spacing={1} flexWrap="wrap">
          <Chip size="small" variant="outlined" label={`${chain.rows.length} strikes listed`} />
          <Chip size="small" variant="outlined" label={`${subscribedCount} live`} />
          <Chip size="small" variant="outlined" label={`step ${formatQty(Number(chain.strikeStep))}`} />
          {atmStrike !== null ? (
            <Chip size="small" color="primary" variant="outlined" label={`ATM ${formatQty(atmStrike)}`} />
          ) : null}
          <Chip
            size="small"
            variant="outlined"
            color={synthetic ? 'warning' : 'default'}
            label={synthetic ? 'greeks: Black-76 (synthetic)' : 'greeks: Dhan chain, 3s'}
          />
        </Stack>
      ) : null}

      <Alert severity="info" variant="outlined" sx={{ py: 0.5 }}>
        Only strikes near the money are subscribed to the live feed. Rows outside that
        window show no prices until the underlying moves toward them.
      </Alert>

      {loading ? (
        <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 200 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Card sx={{ overflow: 'hidden' }}>
          <TableContainer sx={{ maxHeight: 'calc(100vh - 380px)' }}>
            <Table stickyHeader size="small">
              <TableHead>
                <TableRow>
                  <TableCell align="center" colSpan={showAllGreeks ? 11 : 9} sx={{ fontWeight: 700 }}>
                    CALLS
                  </TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>
                    STRIKE
                  </TableCell>
                  <TableCell align="center" colSpan={showAllGreeks ? 11 : 9} sx={{ fontWeight: 700 }}>
                    PUTS
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell align="right">OI</TableCell>
                  <TableCell align="right">OI Chg</TableCell>
                  <TableCell align="right">Vol</TableCell>
                  <TableCell align="right">IV %</TableCell>
                  <TableCell align="right">Delta</TableCell>
                  <TableCell align="right">Theta</TableCell>
                  {showAllGreeks ? (
                    <>
                      <TableCell align="right">Gamma</TableCell>
                      <TableCell align="right">Vega</TableCell>
                    </>
                  ) : null}
                  <TableCell align="right">Bid</TableCell>
                  <TableCell align="right">Ask</TableCell>
                  <TableCell align="right">LTP</TableCell>

                  <TableCell align="center" />

                  <TableCell align="right">LTP</TableCell>
                  <TableCell align="right">Bid</TableCell>
                  <TableCell align="right">Ask</TableCell>
                  {showAllGreeks ? (
                    <>
                      <TableCell align="right">Vega</TableCell>
                      <TableCell align="right">Gamma</TableCell>
                    </>
                  ) : null}
                  <TableCell align="right">Theta</TableCell>
                  <TableCell align="right">Delta</TableCell>
                  <TableCell align="right">IV %</TableCell>
                  <TableCell align="right">Vol</TableCell>
                  <TableCell align="right">OI Chg</TableCell>
                  <TableCell align="right">OI</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((row) => {
                  const isAtm = row.strike === atmStrike;
                  return (
                    <ChainRow
                      key={row.strike}
                      ref={isAtm ? atmRowRef : undefined}
                      strike={row.strike}
                      call={row.call}
                      put={row.put}
                      isAtm={isAtm}
                      underlying={underlying}
                      showAllGreeks={showAllGreeks}
                      onSelect={setTicketContract}
                    />
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Card>
      )}

      <OrderTicket
        open={Boolean(ticketContract)}
        contract={ticketContract}
        onClose={() => setTicketContract(null)}
      />
    </Stack>
  );
}
