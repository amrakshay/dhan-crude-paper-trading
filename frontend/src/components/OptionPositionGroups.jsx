import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { optionsTraderApi } from '../api/optionsTrader';
import { positionsApi } from '../api/trading';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { chipTone } from '../theme/chipTone';
import { formatPrice, formatQty, formatSignedPrice } from '../utils/format';

/**
 * Open option positions grouped by underlying and expiry, each group with its
 * SPAN and exposure margin (OPTIONS_TRADER_PLAN.md s.4.2).
 *
 * Polls at 2 s like Positions (frontend/CLAUDE.md s.2): P&L and margin are
 * server-computed. "No mark" is shown as such, never as 0.00 (s.3).
 *
 * Exiting the LONG leg of a hedge first can be refused with Dhan's "Hedge
 * Break Alert"; the server's own sentence is shown in a dialog, with Exit all
 * -- which covers the shorts first -- one click away.
 */
const POLL_MS = 2000;

function rupees(value) {
  if (value === null || value === undefined) return '—';
  return `₹${formatPrice(value)}`;
}

export default function OptionPositionGroups({ refreshKey }) {
  const theme = useTheme();
  const { activeId: portfolioId, refresh: refreshPortfolio } = useActivePortfolio();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [hedgeBreak, setHedgeBreak] = useState(null);

  const load = useCallback(async () => {
    if (!portfolioId) return;
    try {
      setData(await optionsTraderApi.positions(portfolioId));
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    }
  }, [portfolioId]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, refreshKey]);

  const exitLeg = async (group, leg) => {
    setBusy(leg.positionId);
    try {
      await positionsApi.close(leg.positionId, { orderType: 'MARKET' });
      refreshPortfolio?.();
      await load();
    } catch (exitError) {
      if (String(exitError.message).startsWith('Hedge Break Alert')) {
        setHedgeBreak({ group, message: exitError.message });
      } else {
        setError(exitError.message);
      }
    } finally {
      setBusy(null);
    }
  };

  const exitAll = async (group) => {
    setBusy(`${group.underlying}-${group.expiry}`);
    setHedgeBreak(null);
    try {
      const result = await optionsTraderApi.exitGroup({
        portfolioId,
        underlying: group.underlying,
        expiry: group.expiry,
      });
      const refused = (result.orders ?? []).filter((order) => order.outcome === 'REJECTED');
      if (refused.length) setError(refused.map((order) => `${order.tradingSymbol}: ${order.reason}`).join(' · '));
      refreshPortfolio?.();
      await load();
    } catch (exitError) {
      setError(exitError.message);
    } finally {
      setBusy(null);
    }
  };

  if (!portfolioId) return null;
  const margin = data?.margin;
  const span = data?.span;

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
          <Typography variant="h5">Option positions</Typography>
          <Typography variant="caption" color="text.secondary">grouped by underlying and expiry · every module in this portfolio</Typography>
          <Box sx={{ flexGrow: 1 }} />
          {margin ? (
            <Typography variant="body2" className="numeric">
              Margin (estimate) <b>{rupees(margin.blocked)}</b> · SPAN {rupees(margin.brokerSpan)} · exposure {rupees(margin.exposure)}
              {margin.expiryDayAddon ? ` · expiry-day ${rupees(margin.expiryDayAddon)}` : ''}
            </Typography>
          ) : null}
        </Stack>
        {span && !span.loaded ? (
          <Alert severity="warning" sx={{ mb: 1 }}>
            No NSE SPAN file is in force{span.lastError ? ` (${span.lastError})` : ''}. Margin uses the
            scenario fallback until one loads.
          </Alert>
        ) : null}
        {span?.loaded ? (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            NSE SPAN file {span.businessDate}, {span.snapshotLabel ?? span.snapshot} ({formatQty(span.contracts)} contracts)
            {span.policy === 'beginning_of_day'
              ? ', the snapshot Dhan margins against.'
              : '; re-margined on each intraday snapshot.'} Blocked money is margin
            less net option value: a long blocks only its premium, a short's premium stays unspendable.
          </Typography>
        ) : null}
        {error ? <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1 }}>{error}</Alert> : null}

        {data === null ? (
          <CircularProgress size={20} />
        ) : data.groups.length === 0 ? (
          <Typography variant="body2" color="text.secondary">No open option positions in this portfolio.</Typography>
        ) : (
          <Stack spacing={2}>
            {data.groups.map((group) => {
              const groupMargin = data.marginByUnderlying?.[group.underlying];
              const pnl = group.unrealizedPnl;
              const key = `${group.underlying}-${group.expiry}`;
              return (
                <Box key={key} sx={{ border: 1, borderColor: 'divider', borderRadius: 1 }}>
                  <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap sx={{ px: 1.5, py: 1, bgcolor: 'action.hover' }}>
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{group.underlying} · {group.expiry}</Typography>
                    {group.hedged ? <Chip size="small" variant="outlined" label="Hedged" sx={chipTone(theme, 'done')} /> : null}
                    {group.nakedShort ? <Chip size="small" variant="outlined" label="Naked short" sx={chipTone(theme, 'problem')} /> : null}
                    <Box sx={{ flexGrow: 1 }} />
                    <Typography variant="body2" className="numeric" sx={{ color: pnl === null ? 'text.secondary' : pnl >= 0 ? theme.market.up : theme.market.down }}>
                      {pnl === null ? 'no mark' : formatSignedPrice(pnl)}
                    </Typography>
                    {groupMargin ? (
                      <Typography variant="caption" color="text.secondary" className="numeric">
                        {group.underlying} margin {rupees(groupMargin.blocked)} (SPAN {rupees(groupMargin.brokerSpan)}, exp {rupees(groupMargin.exposure)})
                      </Typography>
                    ) : null}
                    <Button size="small" color="error" variant="outlined" disabled={busy === key} onClick={() => exitAll(group)}>
                      Exit all
                    </Button>
                  </Stack>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>Contract</TableCell><TableCell>Product</TableCell><TableCell align="right">Net qty</TableCell>
                        <TableCell align="right">Avg</TableCell><TableCell align="right">Mark</TableCell>
                        <TableCell align="right">P&amp;L</TableCell><TableCell align="right">Δ</TableCell><TableCell />
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {group.legs.map((leg) => {
                        const long = leg.netQuantity > 0;
                        return (
                          <TableRow key={leg.positionId} className="numeric">
                            <TableCell>
                              <Chip size="small" variant="outlined" label={long ? 'LONG' : 'SHORT'} sx={{ mr: 1, ...chipTone(theme, long ? 'done' : 'problem') }} />
                              {formatQty(leg.strike)} {leg.optionType}
                            </TableCell>
                            <TableCell>
                              {leg.product ? (
                                <Chip size="small" variant="outlined" label={leg.product === 'INTRADAY' ? 'Intraday' : 'Carry-forward'} sx={chipTone(theme, leg.product === 'INTRADAY' ? 'pending' : 'neutral')} />
                              ) : '—'}
                            </TableCell>
                            <TableCell align="right">{formatQty(leg.netQuantity)}</TableCell>
                            <TableCell align="right">{formatPrice(leg.averagePrice)}</TableCell>
                            <TableCell align="right">{leg.mark === null ? 'no mark' : formatPrice(leg.mark)}</TableCell>
                            <TableCell align="right" sx={{ color: leg.unrealizedPnl === null ? 'text.secondary' : leg.unrealizedPnl >= 0 ? theme.market.up : theme.market.down }}>
                              {leg.unrealizedPnl === null ? 'no mark' : formatSignedPrice(leg.unrealizedPnl)}
                            </TableCell>
                            <TableCell align="right">{leg.delta === null || leg.delta === undefined ? '—' : Number(leg.delta).toFixed(2)}</TableCell>
                            <TableCell align="right">
                              <Button size="small" variant="outlined" disabled={busy === leg.positionId} onClick={() => exitLeg(group, leg)}>Exit</Button>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </Box>
              );
            })}
          </Stack>
        )}
      </CardContent>

      <Dialog open={!!hedgeBreak} onClose={() => setHedgeBreak(null)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ color: theme.market.down }}>Hedge Break Alert</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2">{hedgeBreak?.message}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            The exit was not sent. Exit all covers the shorts first, so margin never spikes on the way out.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setHedgeBreak(null)}>Close</Button>
          <Button color="error" variant="contained" disableElevation onClick={() => exitAll(hedgeBreak.group)}>
            Exit all (shorts first)
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}
