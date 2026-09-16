/**
 * The trading strip under the chart: what a click would buy, the buttons, and
 * the live position.
 *
 * ## Why the cost is on screen before the click
 *
 * Order entry must show the estimated charges and the net debit BEFORE the
 * order is sent (frontend/CLAUDE.md section 3). One-click trading has no
 * confirm step, so the cost is shown continuously instead: the row above each
 * button is what that button would buy right now, priced through the same fill
 * simulation the order ticket uses.
 *
 * ## Why a Sell is green-to-red but never a short
 *
 * A Sell click buys a PUT. There is no written option anywhere in this feature,
 * so the worst case is always the premium paid.
 *
 * ## P&L
 *
 * The server owns realised P&L and charges (it computes them from the fills).
 * The unrealised leg is recomputed here from the live option mark so the number
 * moves with the feed rather than with the 2 s poll -- same formula, same
 * inputs, and the server's own figure replaces it on every poll.
 */
import { useMemo } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import CloseIcon from '@mui/icons-material/Close';
import { useTheme } from '@mui/material/styles';
import { useMarketRow } from '../market/MarketFeedContext';
import { formatPrice, formatQty } from '../utils/format';

function formatMoney(value, fallback = '—') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return fallback;
  return Number(value).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatSignedMoney(value) {
  if (value === null || value === undefined) return '—';
  const number = Number(value);
  return `${number >= 0 ? '+' : '−'}₹${formatMoney(Math.abs(number))}`;
}

/** What one button would buy right now, and what it would cost. */
function SideCost({ leg }) {
  if (!leg || leg.error) {
    return (
      <Typography variant="caption" color="text.secondary">
        {leg?.error ?? 'No quote'}
      </Typography>
    );
  }
  return (
    <Stack spacing={0.25}>
      <Typography variant="caption" color="text.secondary" className="numeric">
        {leg.tradingSymbol} @ {formatPrice(leg.estimatedPrice)}
      </Typography>
      <Typography variant="caption" color="text.secondary" className="numeric">
        debit ₹{formatMoney(Math.abs(Number(leg.netAmount)))} (incl. ₹
        {formatMoney(leg.estimatedCharges)} charges)
      </Typography>
      {leg.wouldPartiallyFill ? (
        <Typography variant="caption" sx={{ color: 'warning.main' }}>
          would only partially fill
        </Typography>
      ) : null}
    </Stack>
  );
}

export default function ChartTradeHud({
  trade,
  preview,
  expiry,
  onExpiryChange,
  busy,
  error,
  onClick,
  onClose,
  onAddStop,
  onAddTarget,
  disabled,
  disabledReason,
}) {
  const theme = useTheme();
  const optionRow = useMarketRow(trade?.optionSecurityId);

  // Live unrealised leg, from the same inputs the server uses. The server's
  // own figure lands on the next poll and wins.
  const livePnl = useMemo(() => {
    if (!trade) return null;
    const mark = optionRow?.ltp;
    if (mark == null || trade.averagePrice == null || !trade.quantity) return trade.netPnl;
    const unrealized = (Number(mark) - Number(trade.averagePrice)) * Number(trade.quantity);
    return unrealized + Number(trade.realizedPnl ?? 0) - Number(trade.charges ?? 0);
  }, [trade, optionRow]);

  const pnlColour =
    livePnl === null || livePnl === undefined
      ? 'text.primary'
      : Number(livePnl) >= 0
        ? theme.market.up
        : theme.market.down;

  const expiries = preview?.expiries ?? [];

  return (
    <Stack spacing={1.5} sx={{ mt: 2 }}>
      <Divider />

      {error ? <Alert severity="error">{error}</Alert> : null}
      {disabled && disabledReason ? <Alert severity="info">{disabledReason}</Alert> : null}

      <Stack direction="row" spacing={2} alignItems="flex-start" flexWrap="wrap">
        {/* --- the two buttons, each with what it would cost ------------- */}
        <Stack spacing={0.75} sx={{ minWidth: 260 }}>
          <SideCost leg={preview?.buy} />
          <Button
            fullWidth
            variant="contained"
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <TrendingUpIcon />}
            disabled={busy || disabled || !!preview?.buy?.error}
            onClick={() => onClick('BUY')}
            sx={{
              bgcolor: theme.market.up,
              '&:hover': { bgcolor: theme.market.up, filter: 'brightness(0.92)' },
            }}
          >
            {trade?.chartSide === 'SELL' ? 'Buy — closes the put' : 'Buy — ATM call'}
          </Button>
        </Stack>

        <Stack spacing={0.75} sx={{ minWidth: 260 }}>
          <SideCost leg={preview?.sell} />
          <Button
            fullWidth
            variant="contained"
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <TrendingDownIcon />}
            disabled={busy || disabled || !!preview?.sell?.error}
            onClick={() => onClick('SELL')}
            sx={{
              bgcolor: theme.market.down,
              '&:hover': { bgcolor: theme.market.down, filter: 'brightness(0.92)' },
            }}
          >
            {trade?.chartSide === 'BUY' ? 'Sell — closes the call' : 'Sell — ATM put'}
          </Button>
        </Stack>

        <TextField
          select
          size="small"
          label="Expiry"
          value={expiry ?? ''}
          onChange={(event) => onExpiryChange(event.target.value || null)}
          sx={{ minWidth: 160 }}
          disabled={busy || !expiries.length}
          helperText="Option expiry to buy"
        >
          {expiries.map((value) => (
            <MenuItem key={value} value={value}>
              {value}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      {/* --- the open position ------------------------------------------ */}
      {trade ? (
        <Stack
          direction="row"
          spacing={2}
          alignItems="center"
          flexWrap="wrap"
          sx={{
            p: 1.5,
            borderRadius: 1,
            border: `1px solid ${theme.palette.divider}`,
            bgcolor: Number(livePnl) >= 0 ? theme.market.upSoft : theme.market.downSoft,
          }}
        >
          <Chip
            size="small"
            label={trade.chartSide === 'BUY' ? 'LONG CALL' : 'LONG PUT'}
            sx={{ bgcolor: trade.chartSide === 'BUY' ? theme.market.up : theme.market.down, color: '#fff' }}
          />
          <Typography variant="body2" className="numeric">
            {trade.optionSymbol}
          </Typography>
          <Typography variant="caption" color="text.secondary" className="numeric">
            {formatQty(trade.quantity)} @ {formatPrice(trade.averagePrice)} · mark{' '}
            {formatPrice(optionRow?.ltp ?? trade.markPrice)}
          </Typography>

          <Box sx={{ flexGrow: 1 }} />

          <Stack alignItems="flex-end" spacing={0.25}>
            <Typography variant="h5" className="numeric" sx={{ color: pnlColour }}>
              {formatSignedMoney(livePnl)}
            </Typography>
            <Typography variant="caption" color="text.secondary" className="numeric">
              net of ₹{formatMoney(trade.charges)} charges
            </Typography>
          </Stack>

          {/* These buttons PLACE a line at a default distance. Moving one is
              done by dragging its handle on the chart, so a line that already
              exists offers "Reset" rather than pretending the button moves it. */}
          <Tooltip
            title={
              trade.stopLossLevel
                ? 'Put the stop-loss line back at its default distance'
                : 'Place a stop-loss line on the chart, then drag its handle'
            }
          >
            <span>
              <Button size="small" variant="outlined" onClick={onAddStop} disabled={busy}>
                {trade.stopLossLevel ? 'Reset SL' : '+ SL'}
              </Button>
            </span>
          </Tooltip>
          <Tooltip
            title={
              trade.takeProfitLevel
                ? 'Put the take-profit line back at its default distance'
                : 'Place a take-profit line on the chart, then drag its handle'
            }
          >
            <span>
              <Button size="small" variant="outlined" onClick={onAddTarget} disabled={busy}>
                {trade.takeProfitLevel ? 'Reset TP' : '+ TP'}
              </Button>
            </span>
          </Tooltip>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            startIcon={<CloseIcon />}
            onClick={() => onClose(trade.id)}
            disabled={busy}
          >
            Close
          </Button>
        </Stack>
      ) : null}

      <Typography variant="caption" color="text.secondary">
        Paper rupees. A Buy buys the nearest ATM call and a Sell the nearest ATM put — never a
        written option. Stop-loss and take-profit lines are levels of the future; what the option
        is worth when one is hit depends on delta, decay and IV, so a stop limits the level, not
        the rupees.
      </Typography>
    </Stack>
  );
}
