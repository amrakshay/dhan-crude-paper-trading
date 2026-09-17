import { useCallback, useEffect, useMemo, useState } from 'react';
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
  FormControl,
  FormControlLabel,
  Grid,
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
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { positionsApi } from '../api/trading';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { useMarketFeed } from '../market/MarketFeedContext';
import SyntheticBanner from '../components/SyntheticBanner';
import { formatPrice, formatQty } from '../utils/format';

function PnlCell({ value, hasMark = true }) {
  const theme = useTheme();
  if (value === null || value === undefined) {
    return (
      <Tooltip title="No live price for this contract, so mark-to-market is unknown">
        <Typography variant="body2" color="text.disabled">
          no mark
        </Typography>
      </Tooltip>
    );
  }
  const numeric = Number(value);
  return (
    <Typography
      variant="body2"
      className="numeric"
      sx={{ color: numeric >= 0 ? theme.market.up : theme.market.down, fontWeight: 500 }}
    >
      {numeric >= 0 ? '+' : ''}
      {formatPrice(numeric)}
    </Typography>
  );
}

function SummaryTile({ label, value, color, hint }) {
  return (
    <Card>
      <CardContent>
        <Stack spacing={0.5}>
          <Tooltip title={hint ?? ''}>
            <Typography variant="caption" color="text.secondary">
              {label}
            </Typography>
          </Tooltip>
          <Typography variant="h4" className="numeric" sx={{ color }}>
            {value}
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  );
}

export default function PositionsPage() {
  const theme = useTheme();
  const { version } = useMarketFeed();
  const [data, setData] = useState(null);
  const { activeId: portfolioId, active: portfolio, refresh: refreshPortfolio } =
    useActivePortfolio();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [includeClosed, setIncludeClosed] = useState(false);
  const [closing, setClosing] = useState(null);
  const [closeLots, setCloseLots] = useState(1);
  const [closeType, setCloseType] = useState('MARKET');
  const [closeLimit, setCloseLimit] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await positionsApi.list(includeClosed, portfolioId));
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [includeClosed, portfolioId]);

  useEffect(() => {
    load();
  }, [load]);

  // Positions are marked from the same feed the rest of the app uses. Polling
  // the API on every tick would be wasteful, so refresh on a slower cadence and
  // let the feed drive the visible price movement between refreshes.
  useEffect(() => {
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [load]);

  const positions = data?.positions ?? [];
  const summary = data?.summary;

  const openPositions = useMemo(
    () => positions.filter((position) => position.isOpen && position.netQuantity !== 0),
    [positions],
  );
  const closedPositions = useMemo(
    () => positions.filter((position) => !position.isOpen || position.netQuantity === 0),
    [positions],
  );

  const startClose = (position) => {
    setClosing(position);
    setCloseLots(Math.abs(position.netLots ?? 1));
    setCloseType('MARKET');
    setCloseLimit('');
  };

  const submitClose = async () => {
    setSubmitting(true);
    try {
      await positionsApi.close(closing.id, {
        lots: Number(closeLots),
        orderType: closeType,
        limitPrice: closeType === 'LIMIT' ? closeLimit : undefined,
      });
      setClosing(null);
      await load();
    } catch (closeError) {
      setError(closeError.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
        <CircularProgress />
      </Box>
    );
  }

  const netPnl = summary?.netPnl !== null && summary?.netPnl !== undefined ? Number(summary.netPnl) : null;

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h2">Positions</Typography>
          <Typography variant="body2" color="text.secondary">
            Live mark-to-market on open paper positions
          </Typography>
        </Box>
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={includeClosed}
              onChange={(event) => setIncludeClosed(event.target.checked)}
            />
          }
          label={<Typography variant="body2">Show closed</Typography>}
        />
      </Stack>

      <SyntheticBanner />
      {error ? <Alert severity="error">{error}</Alert> : null}

      {summary ? (
        <Grid container spacing={2}>
          <Grid item xs={6} md={3}>
            <SummaryTile label="Open positions" value={summary.openPositions} />
          </Grid>
          <Grid item xs={6} md={3}>
            <SummaryTile
              label="Realised P&L (gross)"
              value={formatPrice(Number(summary.totalRealizedPnl))}
              color={Number(summary.totalRealizedPnl) >= 0 ? theme.market.up : theme.market.down}
              hint="Gross of charges"
            />
          </Grid>
          <Grid item xs={6} md={3}>
            <SummaryTile
              label="Unrealised P&L"
              value={
                summary.totalUnrealizedPnl === null
                  ? 'no marks'
                  : formatPrice(Number(summary.totalUnrealizedPnl))
              }
              color={
                summary.totalUnrealizedPnl === null
                  ? 'text.disabled'
                  : Number(summary.totalUnrealizedPnl) >= 0
                    ? theme.market.up
                    : theme.market.down
              }
              hint="Mark-to-market on open quantity"
            />
          </Grid>
          <Grid item xs={6} md={3}>
            <SummaryTile
              label="Net P&L (after charges)"
              value={netPnl === null ? '—' : formatPrice(netPnl)}
              color={netPnl === null ? 'text.disabled' : netPnl >= 0 ? theme.market.up : theme.market.down}
              hint={`Realised + unrealised − charges (${formatPrice(Number(summary.totalCharges))} charges)`}
            />
          </Grid>
        </Grid>
      ) : null}

      {summary?.positionsWithoutMarks > 0 ? (
        <Alert severity="warning">
          {summary.positionsWithoutMarks} open position(s) have no live price, so their
          mark-to-market is unknown and excluded from the totals above. They are likely outside
          the subscribed strike window.
        </Alert>
      ) : null}

      {openPositions.length === 0 ? (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary">
              No open positions. Click a strike on the option chain to place an order.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <TableContainer component={Card}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Contract</TableCell>
                <TableCell align="right">Net qty</TableCell>
                <TableCell align="right">Lots</TableCell>
                <TableCell align="right">Avg price</TableCell>
                <TableCell align="right">Mark</TableCell>
                <TableCell align="right">Unrealised</TableCell>
                <TableCell align="right">Realised</TableCell>
                <TableCell align="right">Charges</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {openPositions.map((position) => (
                <TableRow key={position.id} className="numeric">
                  <TableCell>
                    <Stack spacing={0.25}>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {position.tradingSymbol}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {position.expiryDate}
                      </Typography>
                      {position.strategyEnabled === false ? (
                        <Tooltip title="This strategy is switched off. The position is real and still counts, but nothing is marking it — the mark is blank rather than stale.">
                          <Chip
                            size="small"
                            variant="outlined"
                            color="warning"
                            label={`${position.strategyLabel ?? position.strategyKey} · off`}
                            sx={{ alignSelf: 'flex-start', mt: 0.5 }}
                          />
                        </Tooltip>
                      ) : null}
                    </Stack>
                  </TableCell>
                  <TableCell align="right">
                    <Chip
                      size="small"
                      label={position.netQuantity > 0 ? 'LONG' : 'SHORT'}
                      sx={{
                        bgcolor: position.netQuantity > 0 ? theme.market.upSoft : theme.market.downSoft,
                        color: position.netQuantity > 0 ? theme.market.up : theme.market.down,
                        mr: 1,
                      }}
                    />
                    {formatQty(Math.abs(position.netQuantity))}
                  </TableCell>
                  <TableCell align="right">{Math.abs(position.netLots ?? 0)}</TableCell>
                  <TableCell align="right">{formatPrice(Number(position.averagePrice))}</TableCell>
                  <TableCell align="right">
                    {position.hasMark ? formatPrice(Number(position.markPrice)) : '—'}
                  </TableCell>
                  <TableCell align="right">
                    <PnlCell value={position.unrealizedPnl} hasMark={position.hasMark} />
                  </TableCell>
                  <TableCell align="right">
                    <PnlCell value={position.realizedPnl} />
                  </TableCell>
                  <TableCell align="right">{formatPrice(Number(position.totalCharges))}</TableCell>
                  <TableCell align="right">
                    <Button size="small" variant="outlined" onClick={() => startClose(position)}>
                      Close
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {includeClosed && closedPositions.length > 0 ? (
        <Stack spacing={1}>
          <Typography variant="h4">Closed</Typography>
          <TableContainer component={Card}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Contract</TableCell>
                  <TableCell align="right">Bought</TableCell>
                  <TableCell align="right">Sold</TableCell>
                  <TableCell align="right">Realised (gross)</TableCell>
                  <TableCell align="right">Charges</TableCell>
                  <TableCell align="right">Net</TableCell>
                  <TableCell align="right">Closed</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {closedPositions.map((position) => (
                  <TableRow key={position.id} className="numeric">
                    <TableCell>{position.tradingSymbol}</TableCell>
                    <TableCell align="right">{formatQty(position.buyQuantity ?? 0)}</TableCell>
                    <TableCell align="right">{formatQty(position.sellQuantity ?? 0)}</TableCell>
                    <TableCell align="right">
                      <PnlCell value={position.realizedPnl} />
                    </TableCell>
                    <TableCell align="right">{formatPrice(Number(position.totalCharges))}</TableCell>
                    <TableCell align="right">
                      <PnlCell
                        value={Number(position.realizedPnl) - Number(position.totalCharges)}
                      />
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="caption" color="text.secondary">
                        {position.closedAt ? new Date(position.closedAt).toLocaleString('en-IN') : '—'}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Stack>
      ) : null}

      <Dialog open={Boolean(closing)} onClose={() => setClosing(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Close {closing?.tradingSymbol}</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              Open: {formatQty(Math.abs(closing?.netQuantity ?? 0))} barrels (
              {Math.abs(closing?.netLots ?? 0)} lots).{' '}
              {closing?.netQuantity > 0 ? 'Closing will SELL.' : 'Closing will BUY.'}
            </Typography>
            <TextField
              label="Lots to close"
              type="number"
              size="small"
              value={closeLots}
              onChange={(event) => setCloseLots(event.target.value)}
              inputProps={{ min: 1, max: Math.abs(closing?.netLots ?? 1), step: 1 }}
              helperText={`Maximum ${Math.abs(closing?.netLots ?? 0)} lots`}
            />
            <FormControl size="small" fullWidth>
              <InputLabel id="close-type-label">Order type</InputLabel>
              <Select
                labelId="close-type-label"
                label="Order type"
                value={closeType}
                onChange={(event) => setCloseType(event.target.value)}
              >
                <MenuItem value="MARKET">Market</MenuItem>
                <MenuItem value="LIMIT">Limit</MenuItem>
              </Select>
            </FormControl>
            {closeType === 'LIMIT' ? (
              <TextField
                label="Limit price"
                type="number"
                size="small"
                value={closeLimit}
                onChange={(event) => setCloseLimit(event.target.value)}
                inputProps={{ step: 0.1, min: 0 }}
              />
            ) : null}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 2 }}>
          <Button color="inherit" onClick={() => setClosing(null)}>
            Cancel
          </Button>
          <Button variant="contained" onClick={submitClose} disabled={submitting}>
            {submitting ? 'Closing…' : 'Confirm close'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
