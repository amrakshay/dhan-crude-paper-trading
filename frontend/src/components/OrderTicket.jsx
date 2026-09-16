import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { ordersApi } from '../api/trading';
import { useMarketRow } from '../market/MarketFeedContext';
import ChargesBreakdown from './ChargesBreakdown';
import { formatPrice, formatQty } from '../utils/format';

/**
 * Order ticket.
 *
 * The rule this screen enforces: estimated charges and the net debit/credit are
 * shown BEFORE the order can be confirmed. The preview comes from the same fill
 * simulation the real order will run, against the same book, so what is shown
 * is what will happen -- including a warning when the order would only
 * partially fill or would rest instead of filling.
 */
export default function OrderTicket({ open, contract, onClose, onPlaced, defaultSide = 'BUY' }) {
  const theme = useTheme();
  const [side, setSide] = useState(defaultSide);
  const [orderType, setOrderType] = useState('MARKET');
  const [lots, setLots] = useState(1);
  const [limitPrice, setLimitPrice] = useState('');
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [error, setError] = useState(null);

  const securityId = contract?.securityId;
  const row = useMarketRow(securityId);
  const previewSeq = useRef(0);

  useEffect(() => {
    if (!open) return;
    setSide(defaultSide);
    setOrderType('MARKET');
    setLots(1);
    setLimitPrice('');
    setPreview(null);
    setError(null);
  }, [open, securityId, defaultSide]);

  // Seed the limit price from the touch the trader would be crossing.
  useEffect(() => {
    if (orderType !== 'LIMIT' || limitPrice !== '' || !row) return;
    const seed = side === 'BUY' ? row.ask ?? row.ltp : row.bid ?? row.ltp;
    if (seed) setLimitPrice(String(Number(seed).toFixed(2)));
  }, [orderType, side, row, limitPrice]);

  const runPreview = useCallback(async () => {
    if (!securityId || !lots || lots < 1) return;
    if (orderType === 'LIMIT' && !limitPrice) return;

    const seq = ++previewSeq.current;
    setPreviewing(true);
    setError(null);
    try {
      const result = await ordersApi.preview({
        securityId,
        side,
        orderType,
        lots: Number(lots),
        limitPrice: orderType === 'LIMIT' ? limitPrice : undefined,
      });
      // Ignore a stale response that lost the race with a newer edit.
      if (seq === previewSeq.current) setPreview(result);
    } catch (previewError) {
      if (seq === previewSeq.current) {
        setError(previewError.message);
        setPreview(null);
      }
    } finally {
      if (seq === previewSeq.current) setPreviewing(false);
    }
  }, [securityId, side, orderType, lots, limitPrice]);

  // Re-preview on input change, debounced so typing a limit price does not
  // hammer the endpoint.
  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(runPreview, 250);
    return () => window.clearTimeout(timer);
  }, [open, runPreview]);

  const handlePlace = async () => {
    setPlacing(true);
    setError(null);
    try {
      const order = await ordersApi.place({
        securityId,
        side,
        orderType,
        lots: Number(lots),
        limitPrice: orderType === 'LIMIT' ? limitPrice : undefined,
      });
      onPlaced?.(order);
      onClose?.();
    } catch (placeError) {
      setError(placeError.message);
    } finally {
      setPlacing(false);
    }
  };

  const sideColor = side === 'BUY' ? theme.market.up : theme.market.down;
  const netAmount = preview ? Number(preview.netAmount) : null;
  const canPlace =
    !!preview && !previewing && !placing && !preview.rejectionReason && Number(lots) >= 1;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Stack spacing={0.5}>
          <Typography variant="h4">{contract?.tradingSymbol ?? 'Order'}</Typography>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
            <Typography variant="caption" color="text.secondary" className="numeric">
              LTP {formatPrice(row?.ltp)}
            </Typography>
            <Typography variant="caption" sx={{ color: theme.market.up }} className="numeric">
              Bid {formatPrice(row?.bid)}
            </Typography>
            <Typography variant="caption" sx={{ color: theme.market.down }} className="numeric">
              Ask {formatPrice(row?.ask)}
            </Typography>
            <Chip
              size="small"
              variant="outlined"
              label={`lot ${formatQty(contract?.lotSize ?? row?.lotSize)} bbl`}
            />
          </Stack>
        </Stack>
      </DialogTitle>

      <DialogContent dividers>
        <Stack spacing={2.5}>
          <ToggleButtonGroup
            exclusive
            fullWidth
            value={side}
            onChange={(_event, value) => value && setSide(value)}
          >
            <ToggleButton
              value="BUY"
              sx={{ '&.Mui-selected': { bgcolor: theme.market.upSoft, color: theme.market.up } }}
            >
              Buy
            </ToggleButton>
            <ToggleButton
              value="SELL"
              sx={{ '&.Mui-selected': { bgcolor: theme.market.downSoft, color: theme.market.down } }}
            >
              Sell
            </ToggleButton>
          </ToggleButtonGroup>

          <Stack direction="row" spacing={2}>
            <TextField
              label="Lots"
              type="number"
              size="small"
              fullWidth
              value={lots}
              onChange={(event) => setLots(event.target.value)}
              inputProps={{ min: 1, step: 1 }}
              helperText={
                contract?.lotSize
                  ? `${formatQty(Number(lots || 0) * contract.lotSize)} barrels`
                  : ' '
              }
            />
            <FormControl size="small" fullWidth>
              <InputLabel id="order-type-label">Order type</InputLabel>
              <Select
                labelId="order-type-label"
                label="Order type"
                value={orderType}
                onChange={(event) => setOrderType(event.target.value)}
              >
                <MenuItem value="MARKET">Market</MenuItem>
                <MenuItem value="LIMIT">Limit</MenuItem>
              </Select>
            </FormControl>
            <TextField
              label="Limit price"
              type="number"
              size="small"
              fullWidth
              disabled={orderType !== 'LIMIT'}
              value={limitPrice}
              onChange={(event) => setLimitPrice(event.target.value)}
              inputProps={{ step: 0.1, min: 0 }}
            />
          </Stack>

          {error ? <Alert severity="error">{error}</Alert> : null}

          {preview?.rejectionReason ? (
            <Alert severity="error">{preview.rejectionReason}</Alert>
          ) : null}

          {preview?.wouldRest ? (
            <Alert severity="info">
              This order would <strong>rest</strong> in the book. It fills only once the market
              trades through {formatPrice(Number(preview.limitPrice))} — not when it merely
              touches it.
            </Alert>
          ) : null}

          {preview?.wouldPartiallyFill ? (
            <Alert severity="warning">
              The visible book only supports{' '}
              <strong>{formatQty(preview.estimatedFillQuantity)}</strong> of{' '}
              {formatQty(preview.quantity)} barrels. The remainder will not fill.
            </Alert>
          ) : null}

          <Divider />

          {previewing && !preview ? (
            <Box sx={{ display: 'grid', placeItems: 'center', py: 3 }}>
              <CircularProgress size={24} />
            </Box>
          ) : preview ? (
            <Stack spacing={2}>
              <Stack direction="row" justifyContent="space-between">
                <Typography variant="body2" color="text.secondary">
                  {preview.wouldRest ? 'Limit price' : 'Estimated fill'}
                </Typography>
                <Typography variant="body2" className="numeric" sx={{ fontWeight: 600 }}>
                  {formatPrice(Number(preview.estimatedPrice))}
                  {preview.fills?.length > 1 ? (
                    <Typography component="span" variant="caption" color="text.secondary">
                      {' '}
                      (across {preview.fills.length} levels)
                    </Typography>
                  ) : null}
                </Typography>
              </Stack>

              <Stack direction="row" justifyContent="space-between">
                <Typography variant="body2" color="text.secondary">
                  Premium value
                </Typography>
                <Typography variant="body2" className="numeric">
                  {formatPrice(Number(preview.grossValue))}
                </Typography>
              </Stack>

              <ChargesBreakdown charges={preview.charges} dense />

              <Divider />

              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="subtitle1">
                  Net {netAmount !== null && netAmount < 0 ? 'debit' : 'credit'}
                </Typography>
                <Typography
                  variant="h5"
                  className="numeric"
                  sx={{ color: netAmount !== null && netAmount < 0 ? theme.market.down : theme.market.up }}
                >
                  {formatPrice(Math.abs(netAmount ?? 0))}
                </Typography>
              </Stack>
            </Stack>
          ) : null}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handlePlace}
          disabled={!canPlace}
          sx={{ bgcolor: sideColor, '&:hover': { bgcolor: sideColor, filter: 'brightness(0.9)' } }}
        >
          {placing ? 'Placing…' : `${side} ${lots} lot${Number(lots) === 1 ? '' : 's'}`}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
