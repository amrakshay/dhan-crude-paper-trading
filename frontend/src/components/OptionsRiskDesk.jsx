import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  Stack,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { optionsTraderApi } from '../api/optionsTrader';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { chipTone } from '../theme/chipTone';
import { formatIstDateTime, formatQty } from '../utils/format';

/**
 * The risk desk's record for the active portfolio (OPTIONS_TRADER_PLAN.md
 * s.4.6): what it squared off, why, and whether each exit filled.
 *
 * Its RULES are read from the server (the margin card), never restated here
 * (frontend/CLAUDE.md s.5d). Polls at 10 s: the desk acts at most every few
 * seconds and its record is history, not tick data. "Nothing yet" is said
 * in words, because an empty list reads like a broken one.
 */
const POLL_MS = 10000;
const RULE_LABELS = {
  INTRADAY_SQUARE_OFF: 'Intraday square-off',
  MTM_LOSS: 'MTM loss',
  MARGIN_SHORTFALL: 'Margin shortfall',
};
const OUTCOME_TONE = { DONE: 'done', PARTIAL: 'pending', FAILED: 'problem' };

export default function OptionsRiskDesk({ refreshKey }) {
  const theme = useTheme();
  const { activeId: portfolioId } = useActivePortfolio();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!portfolioId) return;
    try {
      setData(await optionsTraderApi.riskDesk(portfolioId));
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

  if (!portfolioId) return null;
  const rules = data?.rules;
  const status = data?.status;

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
          <Typography variant="h5">Risk desk</Typography>
          {status ? (
            <Chip
              size="small"
              variant="outlined"
              label={status.enabled ? 'watching' : 'switched off'}
              sx={chipTone(theme, status.enabled ? 'done' : 'neutral')}
            />
          ) : null}
          {rules ? (
            <Typography variant="caption" color="text.secondary">
              Squares off Intraday positions at {rules.intradaySquareOffAt} IST (no new Intraday
              from {rules.intradayEntryCutoffAt}), closes the option book on an unrealised loss of{' '}
              {Math.round(rules.mtmLossFraction * 100)}% of its margin, and cuts the largest
              consumer on a margin shortfall. Like Dhan's, it does not ask first.
            </Typography>
          ) : null}
        </Stack>
        {error ? <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert> : null}
        {data && data.actions.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            The risk desk has not had to act on this portfolio.
          </Typography>
        ) : null}
        <Stack spacing={1.5}>
          {(data?.actions ?? []).map((action) => (
            <Box key={action.id} sx={{ borderLeft: 3, borderColor: action.outcome === 'DONE' ? 'divider' : theme.market.down, pl: 1.5 }}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{formatIstDateTime(action.createdAt)}</Typography>
                <Chip size="small" variant="outlined" label={RULE_LABELS[action.rule] ?? action.rule} sx={chipTone(theme, 'pending')} />
                <Chip size="small" variant="outlined" label={action.outcome} sx={chipTone(theme, OUTCOME_TONE[action.outcome] ?? 'neutral')} />
              </Stack>
              <Typography variant="body2" color="text.secondary">{action.detail}</Typography>
              {action.orders.map((order, index) => (
                <Typography key={index} variant="caption" className="numeric" sx={{ display: 'block' }}>
                  {order.side} {formatQty(order.quantity)} {order.tradingSymbol} → {order.outcome}
                  {order.reason ? ` (${order.reason})` : ''}
                </Typography>
              ))}
            </Box>
          ))}
        </Stack>
      </CardContent>
    </Card>
  );
}
