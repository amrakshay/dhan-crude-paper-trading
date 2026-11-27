import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Card, CardContent, Chip, Stack, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { optionsTraderApi } from '../api/optionsTrader';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { chipTone } from '../theme/chipTone';
import { formatIstDateTime, formatPrice, formatQty } from '../utils/format';

/**
 * Baskets with a leg still RESTING (a limit leg not yet filled), so a basket
 * does not vanish from view once its result dialog is closed
 * (OPTIONS_TRADER_PLAN.md Phase 5). Each leg's status is read live from its
 * order. Cancel withdraws the resting legs only: legs that filled stay filled,
 * the way a broker's basket cancel behaves -- nothing is unwound.
 *
 * Shows nothing when no basket is resting: the card is about something
 * waiting, and an empty one would only push the positions down.
 */
const POLL_MS = 5000;
const RESTING = new Set(['OPEN', 'PENDING', 'PARTIALLY_FILLED']);
const OUTCOME_TONE = { FILLED: 'done', OPEN: 'pending', PENDING: 'pending', PARTIALLY_FILLED: 'pending', REJECTED: 'problem', CANCELLED: 'neutral' };

export default function OptionsRestingBaskets({ refreshKey }) {
  const theme = useTheme();
  const { activeId: portfolioId, refresh: refreshPortfolio } = useActivePortfolio();
  const [baskets, setBaskets] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    if (!portfolioId) return;
    try {
      const data = await optionsTraderApi.baskets(portfolioId);
      setBaskets(data.baskets.filter((basket) => basket.legs.some((leg) => RESTING.has(leg.outcome))));
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

  const cancel = async (basketId) => {
    setBusy(basketId);
    try {
      await optionsTraderApi.cancelBasket(basketId);
      refreshPortfolio?.();
      await load();
    } catch (cancelError) {
      setError(cancelError.message);
    } finally {
      setBusy(null);
    }
  };

  if (!portfolioId || (!error && !baskets?.length)) return null;

  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h5" sx={{ mb: 1 }}>Resting baskets</Typography>
        {error ? <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert> : null}
        <Stack spacing={1.5}>
          {(baskets ?? []).map((basket) => (
            <Box key={basket.id} sx={{ borderLeft: 3, borderColor: 'divider', pl: 1.5 }}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>Basket #{basket.id}</Typography>
                <Typography variant="caption" color="text.secondary">{formatIstDateTime(basket.createdAt)}</Typography>
                <Chip size="small" variant="outlined" label={basket.status} sx={chipTone(theme, 'pending')} />
                <Box sx={{ flexGrow: 1 }} />
                <Button size="small" color="error" variant="outlined" disabled={busy === basket.id} onClick={() => cancel(basket.id)}>
                  {busy === basket.id ? 'Cancelling…' : 'Cancel resting legs'}
                </Button>
              </Stack>
              {basket.legs.map((leg) => (
                <Stack key={leg.sequence} direction="row" spacing={1} alignItems="center" className="numeric">
                  <Typography variant="caption" sx={{ minWidth: 36 }}>{leg.side}</Typography>
                  <Typography variant="caption" sx={{ flexGrow: 1 }}>{leg.tradingSymbol}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {leg.orderType === 'LIMIT' ? 'LMT' : 'MKT'} · {leg.quantity ? `${formatQty(leg.filledQuantity)}/${formatQty(leg.quantity)}` : '—'}
                    {leg.averagePrice ? ` @ ${formatPrice(leg.averagePrice)}` : ''}
                  </Typography>
                  <Chip size="small" variant="outlined" label={leg.outcome} sx={chipTone(theme, OUTCOME_TONE[leg.outcome] ?? 'neutral')} />
                </Stack>
              ))}
            </Box>
          ))}
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          Cancel withdraws only the legs still resting. Legs that filled stay in the portfolio.
        </Typography>
      </CardContent>
    </Card>
  );
}
