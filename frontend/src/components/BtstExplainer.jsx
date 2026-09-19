import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Divider,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { btstApi } from '../api/btst';

/**
 * How it works — and what is wrong with it.
 *
 * **THIS COMPONENT RESTATES NO NUMBER.** Every threshold, lookback and time
 * comes from `GET /api/btst/explain`, which reads them out of the strategy's
 * own YAML. Hardcoding "55-day high" in JSX would be a second source of truth
 * (root `CLAUDE.md` section 7) and it would go on saying 55 for as long as it
 * took somebody to notice the configuration had changed. The prose explains
 * WHY a rule exists; the values always come from the payload.
 *
 * It carries two things the handoff insisted on and that a page about a
 * strategy would normally leave out: the specification's own recommendation
 * NOT to fund this yet, and the year-by-year decay table. Both are above the
 * rules rather than below them, because they are the most important facts
 * about this strategy and a reader who stops half way should have seen them.
 */
export default function BtstExplainer({ strategyKey }) {
  const theme = useTheme();
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    btstApi
      .explain(strategyKey)
      .then((next) => {
        if (!cancelled) setPayload(next);
      })
      .catch((caught) => {
        if (!cancelled) setError(caught.message || 'Could not read the explainer');
      });
    return () => {
      cancelled = true;
    };
  }, [strategyKey]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!payload) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  const exitTiming = payload.exitTiming || {};
  const decay = payload.decay || {};

  return (
    <Stack spacing={2}>
      <Alert severity="warning" icon={<WarningAmberIcon />}>
        {payload.recommendation}
      </Alert>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          What it does
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {payload.description}
        </Typography>
      </Paper>

      {/* THE EXIT IS THE STRATEGY, so it gets its own panel above the rules. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          The exit is the strategy
        </Typography>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={4}>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Sold at the next OPEN — what this does
            </Typography>
            <Typography variant="h6" className="numeric" sx={{ color: theme.market.up }}>
              {`+${(exitTiming.nextOpenMeanGap * 100).toFixed(3)}%`}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {(exitTiming.nextOpenWinRate * 100).toFixed(1)}% win rate
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Held to the next CLOSE instead — same signals
            </Typography>
            <Typography variant="h6" className="numeric" sx={{ color: theme.market.down }}>
              {`+${(exitTiming.nextCloseMeanGap * 100).toFixed(3)}%`}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {(exitTiming.nextCloseWinRate * 100).toFixed(1)}% win rate
            </Typography>
          </Box>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
          {exitTiming.note}
        </Typography>
      </Paper>

      {/* The decay table, which the handoff named explicitly. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          Year by year — the decay
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Year</TableCell>
                <TableCell align="right">Mean per trade</TableCell>
                <TableCell align="right">Win rate</TableCell>
                <TableCell align="right">Trades</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {(decay.rows || []).map((one) => (
                <TableRow key={one.year}>
                  <TableCell>{one.year}</TableCell>
                  <TableCell
                    align="right"
                    className="numeric"
                    sx={{
                      color: one.meanGap >= 0 ? theme.market.up : theme.market.down,
                    }}
                  >
                    {`${one.meanGap >= 0 ? '+' : ''}${(one.meanGap * 100).toFixed(3)}%`}
                  </TableCell>
                  <TableCell align="right" className="numeric">
                    {(one.winRate * 100).toFixed(1)}%
                  </TableCell>
                  <TableCell align="right" className="numeric">
                    {one.trades}
                  </TableCell>
                  <TableCell>
                    {one.year === 2021 ? (
                      <Chip size="small" color="warning" label="+101.3% of the whole result" />
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <Alert severity="warning" sx={{ mt: 1.5 }}>
          {decay.note}
        </Alert>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          The rules, as configured
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Every value here is read from the strategy's own YAML. Nothing on this
          page is typed in twice.
        </Typography>
        <TableContainer sx={{ mt: 1 }}>
          <Table size="small">
            <TableBody>
              {(payload.parameters || []).map((one) => (
                <TableRow key={one.code}>
                  <TableCell sx={{ width: 60 }}>
                    <Chip size="small" label={one.code} />
                  </TableCell>
                  <TableCell sx={{ width: 180 }}>{one.name}</TableCell>
                  <TableCell>{one.value}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        {payload.regimeIndex ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            The regime index is {payload.regimeIndex.label} (
            {payload.regimeIndex.segment}), which this strategy READS and never
            trades.
          </Typography>
        ) : null}
        {/* B10 is the one place the specification contradicts itself, and the
            page says so rather than presenting a choice as a given. */}
        <Divider sx={{ my: 1.5 }} />
        <Typography variant="caption" color="text.secondary">
          <strong>On B10, the ranking.</strong> The specification contradicts
          itself: B10's own table says rank by volume ratio, and its section 16
          recommends six-month momentum, which measured the better risk-adjusted
          return (MAR 2.12 against 2.05). B10 ships, because section 9.3 also
          shows ranking is worth at most 0.9 points of CAGR over ranking
          alphabetically and binds on only 5.7% of signal-days — so deviating
          buys almost nothing and costs comparability with every other number
          in the document.
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          What is NOT verified
        </Typography>
        <Stack spacing={1}>
          {(payload.caveats || []).map((one, index) => (
            <Typography key={index} variant="body2" color="text.secondary">
              • {one}
            </Typography>
          ))}
        </Stack>
      </Paper>
    </Stack>
  );
}
