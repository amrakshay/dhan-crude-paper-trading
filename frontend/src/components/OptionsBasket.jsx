import { useEffect, useMemo, useRef, useState } from 'react';
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
  Divider,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';
import { useTheme } from '@mui/material/styles';
import { optionsTraderApi } from '../api/optionsTrader';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { chipTone } from '../theme/chipTone';
import PayoffChart from './PayoffChart';
import { formatPrice, formatQty } from '../utils/format';

/**
 * The basket: legs collected from the chain, previewed on NSE SPAN, placed
 * leg by leg the way Dhan's basket is (OPTIONS_TRADER_PLAN.md D3).
 *
 * frontend/CLAUDE.md s.3 applies: the margin, charges and net premium are on
 * screen BEFORE Place is enabled, every margin figure says "estimate", and a
 * fallback (no SPAN file) is shown as one. The confirm dialog lists the legs in
 * the order they will be SENT -- buys first -- because that order is what
 * decides whether a short gets its hedge benefit.
 */
const PRODUCTS = [
  { value: 'CARRYFORWARD', label: 'Carry-forward', help: 'Held until you exit it, or until expiry.' },
  { value: 'INTRADAY', label: 'Intraday', help: 'Dhan squares Intraday F&O positions off at 15:25 IST.' },
];

const OUTCOME_TONE = {
  FILLED: 'done',
  PARTIALLY_FILLED: 'pending',
  OPEN: 'pending',
  PENDING: 'pending',
  REJECTED: 'problem',
  CANCELLED: 'neutral',
};

function rupees(value) {
  if (value === null || value === undefined) return '—';
  return `₹${formatPrice(value)}`;
}

function MoneyRow({ label, value, strong, tone }) {
  return (
    <Stack direction="row" justifyContent="space-between" className="numeric">
      <Typography variant="body2" color={strong ? 'text.primary' : 'text.secondary'} sx={{ fontWeight: strong ? 600 : 400 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: strong ? 600 : 400, color: tone }}>
        {value}
      </Typography>
    </Stack>
  );
}

export default function OptionsBasket({ legs, setLegs, spot, onPlaced }) {
  const theme = useTheme();
  const { activeId: portfolioId, active: portfolio, refresh: refreshPortfolio } = useActivePortfolio();
  const [product, setProduct] = useState('CARRYFORWARD');
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [result, setResult] = useState(null);
  const seq = useRef(0);

  const payload = useMemo(
    () => ({
      portfolioId,
      product,
      legs: legs.map((leg) => ({
        securityId: leg.securityId,
        side: leg.side,
        lots: leg.lots,
        orderType: leg.orderType,
        limitPrice: leg.orderType === 'LIMIT' && leg.limitPrice ? leg.limitPrice : undefined,
      })),
    }),
    [portfolioId, product, legs],
  );

  useEffect(() => {
    if (!legs.length || !portfolioId) {
      setPreview(null);
      setPreviewError(null);
      return undefined;
    }
    const mine = ++seq.current;
    const timer = window.setTimeout(async () => {
      setPreviewing(true);
      try {
        const data = await optionsTraderApi.previewBasket(payload);
        if (mine === seq.current) {
          setPreview(data);
          setPreviewError(null);
        }
      } catch (error) {
        if (mine === seq.current) {
          setPreview(null);
          setPreviewError(error.message);
        }
      } finally {
        if (mine === seq.current) setPreviewing(false);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [payload, legs.length, portfolioId]);

  const update = (index, patch) =>
    setLegs((current) => current.map((leg, i) => (i === index ? { ...leg, ...patch } : leg)));
  const remove = (index) => setLegs((current) => current.filter((_, i) => i !== index));

  const priceOf = (leg) => {
    const fromPreview = preview?.legs?.find((row) => row.securityId === leg.securityId && row.side === leg.side);
    return fromPreview?.price ?? leg.price ?? null;
  };
  const payoffLegs = legs.map((leg) => ({
    optionType: leg.optionType,
    strike: leg.strike,
    quantity: (leg.side === 'BUY' ? 1 : -1) * leg.lots * leg.lotSize,
    price: leg.orderType === 'LIMIT' && leg.limitPrice ? Number(leg.limitPrice) : priceOf(leg),
  }));
  const sentOrder = [...legs.filter((leg) => leg.side === 'BUY'), ...legs.filter((leg) => leg.side === 'SELL')];

  const canPlace =
    legs.length > 0 && !!portfolioId && !!preview && !previewing && !placing && preview.affordable !== false;

  const place = async () => {
    setPlacing(true);
    try {
      const placed = await optionsTraderApi.placeBasket(payload);
      setResult(placed);
      setConfirming(false);
      refreshPortfolio?.();
      onPlaced?.(placed);
      if (placed.status === 'COMPLETE') setLegs([]);
    } catch (error) {
      setPreviewError(error.message);
      setConfirming(false);
    } finally {
      setPlacing(false);
    }
  };

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Typography variant="h5">Basket</Typography>
          <Typography variant="caption" color="text.secondary">
            {legs.length} leg{legs.length === 1 ? '' : 's'}
          </Typography>
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" onClick={() => setLegs([])} disabled={!legs.length}>Clear</Button>
        </Stack>

        {!legs.length ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            Click <b>B</b> or <b>S</b> on any strike to add a leg. One leg is a single order;
            several are sent together, buys first.
          </Typography>
        ) : (
          <Stack divider={<Divider flexItem />} spacing={1}>
            {legs.map((leg, index) => (
              <Stack key={`${leg.securityId}-${index}`} direction="row" spacing={1} alignItems="center">
                <Chip
                  size="small"
                  variant="outlined"
                  label={leg.side === 'BUY' ? 'B' : 'S'}
                  onClick={() => update(index, { side: leg.side === 'BUY' ? 'SELL' : 'BUY' })}
                  sx={{ ...chipTone(theme, leg.side === 'BUY' ? 'done' : 'problem'), minWidth: 32 }}
                />
                <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap sx={{ fontWeight: 600 }}>{leg.tradingSymbol}</Typography>
                  <Typography variant="caption" color="text.secondary" className="numeric">
                    {formatQty(leg.lots * leg.lotSize)} units · {rupees(priceOf(leg))}
                  </Typography>
                </Box>
                <Stack direction="row" alignItems="center">
                  <IconButton size="small" onClick={() => update(index, { lots: Math.max(1, leg.lots - 1) })}><RemoveIcon fontSize="inherit" /></IconButton>
                  <Typography variant="body2" className="numeric" sx={{ minWidth: 20, textAlign: 'center' }}>{leg.lots}</Typography>
                  <IconButton size="small" onClick={() => update(index, { lots: leg.lots + 1 })}><AddIcon fontSize="inherit" /></IconButton>
                </Stack>
                <Select
                  size="small"
                  value={leg.orderType}
                  onChange={(event) => {
                    const orderType = event.target.value;
                    // Like Dhan's ticket: switching to LMT starts at the
                    // current price rather than an empty box.
                    const start = orderType === 'LIMIT' && !leg.limitPrice ? priceOf(leg) : null;
                    update(index, { orderType, ...(start != null ? { limitPrice: String(start) } : {}) });
                  }}
                  sx={{ fontSize: 12, '& .MuiSelect-select': { py: 0.5 } }}
                >
                  <MenuItem value="MARKET">MKT</MenuItem>
                  <MenuItem value="LIMIT">LMT</MenuItem>
                </Select>
                {leg.orderType === 'LIMIT' ? (
                  <TextField
                    size="small"
                    type="number"
                    value={leg.limitPrice ?? ''}
                    onChange={(event) => update(index, { limitPrice: event.target.value })}
                    sx={{ width: 90 }}
                    inputProps={{ step: 0.05, min: 0, style: { fontSize: 12, padding: '4px 8px' } }}
                  />
                ) : null}
                <IconButton size="small" onClick={() => remove(index)}><CloseIcon fontSize="inherit" /></IconButton>
              </Stack>
            ))}
          </Stack>
        )}

        <Stack spacing={0.5} sx={{ mt: 2 }}>
          <ToggleButtonGroup exclusive fullWidth size="small" value={product} onChange={(_e, value) => value && setProduct(value)}>
            {PRODUCTS.map((option) => (
              <ToggleButton key={option.value} value={option.value}>{option.label}</ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary">
            {PRODUCTS.find((option) => option.value === product)?.help}
          </Typography>
        </Stack>

        {legs.length ? (
          <Box sx={{ mt: 2, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1, bgcolor: 'background.default' }}>
            {previewing && !preview ? <CircularProgress size={18} /> : null}
            {previewError ? <Alert severity="error">{previewError}</Alert> : null}
            {preview ? (
              <Stack spacing={0.5}>
                <MoneyRow label="Overall margin (each leg alone)" value={rupees(preview.overallMargin)} />
                <MoneyRow label="Hedge benefit" value={`− ${rupees(preview.hedgeBenefit)}`} tone={theme.market.up} />
                <MoneyRow label="Final margin required (estimate)" value={rupees(preview.finalMargin)} strong />
                <Typography variant="caption" color="text.secondary" className="numeric">
                  SPAN {rupees(preview.breakdown?.brokerSpan)} · exposure {rupees(preview.breakdown?.exposure)}
                  {preview.breakdown?.expiryDayAddon ? ` · expiry-day ${rupees(preview.breakdown.expiryDayAddon)}` : ''} (portfolio, after)
                </Typography>
                <MoneyRow
                  label={preview.premiumNet >= 0 ? 'Premium received (net)' : 'Premium paid (net)'}
                  value={rupees(Math.abs(preview.premiumNet))}
                  tone={preview.premiumNet >= 0 ? theme.market.up : theme.market.down}
                />
                <MoneyRow label="Charges (estimate)" value={rupees(preview.charges)} />
                <MoneyRow label="Funds this needs" value={rupees(preview.requiredFunds)} strong />
                <MoneyRow label={`Available in ${portfolio?.name ?? 'portfolio'}`} value={rupees(preview.available)} />
                {(preview.legs ?? []).filter((row) => row.refusal).map((row) => (
                  <Alert key={`${row.securityId}-${row.side}`} severity="error" sx={{ mt: 1 }}>
                    {row.refusal} This leg would be rejected; the others would still go.
                  </Alert>
                ))}
                {preview.affordable === false ? (
                  <Alert severity="error" sx={{ mt: 1 }}>
                    Not enough available funds for the whole basket. Legs that do not fit will be rejected.
                  </Alert>
                ) : null}
                {preview.source !== 'span' ? (
                  <Alert severity="warning" sx={{ mt: 1 }}>
                    {preview.source === 'fallback' || preview.source === 'none'
                      ? 'No NSE SPAN file is in force: this margin is the scenario FALLBACK, not SPAN.'
                      : 'Some legs are missing from the SPAN file and use the scenario fallback.'}
                  </Alert>
                ) : (
                  <Typography variant="caption" color="text.secondary">
                    NSE SPAN file {preview.spanFileDate}, applied the way {preview.broker === 'dhan' ? 'Dhan' : 'the exchange'} does. Final margin includes premium paid; premium received is {preview.premiumReceivedCounted ? '' : 'not '}counted.
                  </Typography>
                )}
              </Stack>
            ) : null}
          </Box>
        ) : null}

        {legs.length ? (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Payoff at expiry</Typography>
            <PayoffChart legs={payoffLegs} spot={spot} />
          </Box>
        ) : null}

        <Button
          fullWidth
          variant="contained"
          disableElevation
          sx={{ mt: 2 }}
          disabled={!canPlace}
          onClick={() => setConfirming(true)}
        >
          {legs.length > 1 ? 'Place basket' : 'Place order'}
        </Button>
        {!portfolioId ? (
          <Typography variant="caption" color="text.secondary">Select a portfolio in the header.</Typography>
        ) : null}
      </CardContent>

      <Dialog open={confirming} onClose={() => setConfirming(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Place in {portfolio?.name}</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Each leg is its own paper order, sent <b>in this order</b>. A leg can fill, part-fill,
            rest or be rejected while the others still go.
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>#</TableCell><TableCell>Leg</TableCell><TableCell align="right">Qty</TableCell><TableCell>Type</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {sentOrder.map((leg, index) => (
                <TableRow key={`${leg.securityId}-${index}`} className="numeric">
                  <TableCell>{index + 1}</TableCell>
                  <TableCell>
                    <Chip size="small" variant="outlined" label={leg.side} sx={{ mr: 1, ...chipTone(theme, leg.side === 'BUY' ? 'done' : 'problem') }} />
                    {leg.tradingSymbol}
                  </TableCell>
                  <TableCell align="right">{formatQty(leg.lots * leg.lotSize)}</TableCell>
                  <TableCell>{leg.orderType === 'LIMIT' ? `LMT ${leg.limitPrice}` : 'MKT'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Typography variant="body2" sx={{ mt: 1 }}>
            {PRODUCTS.find((option) => option.value === product)?.label} · final margin {rupees(preview?.finalMargin)} (estimate)
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirming(false)}>Cancel</Button>
          <Button variant="contained" disableElevation onClick={place} disabled={placing}>
            {placing ? 'Placing…' : 'Confirm'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!result} onClose={() => setResult(null)} maxWidth="md" fullWidth>
        <DialogTitle>
          Basket #{result?.id}{' '}
          <Chip
            size="small"
            variant="outlined"
            label={result?.status}
            sx={chipTone(theme, result?.status === 'COMPLETE' ? 'done' : result?.status === 'REJECTED' ? 'problem' : 'pending')}
          />
        </DialogTitle>
        <DialogContent dividers>
          {result && result.status !== 'COMPLETE' ? (
            <Alert severity={result.status === 'REJECTED' ? 'error' : 'warning'} sx={{ mb: 1 }}>
              {result.status === 'REJECTED'
                ? 'No leg executed. Each leg\'s reason is below.'
                : 'Not every leg executed. Nothing was rolled back -- this is how a broker\'s basket behaves. The legs that did execute are live in the portfolio.'}
            </Alert>
          ) : null}
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>#</TableCell><TableCell>Leg</TableCell><TableCell align="right">Filled</TableCell>
                <TableCell align="right">Avg</TableCell><TableCell>Status</TableCell><TableCell>Reason</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(result?.legs ?? []).map((leg) => (
                <TableRow key={leg.sequence} className="numeric">
                  <TableCell>{leg.sequence}</TableCell>
                  <TableCell>
                    <Chip size="small" variant="outlined" label={leg.side} sx={{ mr: 1, ...chipTone(theme, leg.side === 'BUY' ? 'done' : 'problem') }} />
                    {leg.tradingSymbol ?? leg.securityId}
                  </TableCell>
                  <TableCell align="right">{leg.quantity ? `${formatQty(leg.filledQuantity)}/${formatQty(leg.quantity)}` : '—'}</TableCell>
                  <TableCell align="right">{formatPrice(leg.averagePrice)}</TableCell>
                  <TableCell>
                    <Chip size="small" variant="outlined" label={leg.outcome} sx={chipTone(theme, OUTCOME_TONE[leg.outcome] ?? 'neutral')} />
                  </TableCell>
                  <TableCell sx={{ maxWidth: 320 }}>
                    <Typography variant="caption" color="text.secondary">{leg.reason ?? ''}</Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DialogContent>
        <DialogActions><Button onClick={() => setResult(null)}>Close</Button></DialogActions>
      </Dialog>
    </Card>
  );
}
