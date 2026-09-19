import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Stack,
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
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { btstApi } from '../api/btst';
import BtstFunnel from './BtstFunnel';

/**
 * TODAY'S SCAN, as it stands right now.
 *
 * The tab the rotation has no equivalent of, and the reason is the difference
 * between the two strategies: the rotation decides overnight in one shot on
 * finished bars, while this one decides at 15:20 on the session SO FAR — a
 * running high, a running low, a cumulative volume — and that decision forms
 * over the afternoon. Between 14:30 and 15:20 this is the most interesting
 * thing on the screen.
 *
 * It is a READ. It computes the identical funnel the scheduled scan computes,
 * journals nothing and places nothing.
 *
 * **AN EMPTY CANDIDATE LIST IS THE NORMAL STATE.** At about half a signal a
 * session most days produce nothing, so the funnel is always rendered and the
 * empty case reads as a working scan rather than a broken page.
 *
 * It polls faster than the rest of the page — 30 s rather than the journal's
 * 10 s is the wrong way round, so it uses the same 10 s: the funnel genuinely
 * moves during the session, and this is the one tab where a stale number is
 * misleading rather than merely old.
 */

const POLL_MS = 10000;

export default function BtstSignals({ strategyKey, portfolioId }) {
  const theme = useTheme();
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setPayload(await btstApi.signals(strategyKey, portfolioId));
      setError(null);
    } catch (caught) {
      setError(caught.message || 'Could not read today’s scan');
    } finally {
      setLoading(false);
    }
  }, [strategyKey, portfolioId]);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  if (loading && !payload) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  const candidates = payload?.candidates || [];
  const nearMisses = payload?.nearMisses || [];

  return (
    <Stack spacing={2}>
      {error ? <Alert severity="error">{error}</Alert> : null}

      {/* An error from the scan is a RESULT, not a page failure: stale bars,
          no universe, nothing subscribed. Each says what to do. */}
      {payload?.error ? <Alert severity="warning">{payload.error}</Alert> : null}

      {payload?.blocked ? (
        <Alert severity="info" icon={<InfoOutlinedIcon />}>
          Entries are blocked: {payload.blocked}. Candidates are still found and
          still listed — the gate is computed and recorded whether or not it is
          enforced, which is what makes its cost measurable afterwards.
        </Alert>
      ) : null}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
          <Typography variant="subtitle1">The filter funnel, right now</Typography>
          {payload?.fnoExcluded ? (
            <Chip size="small" label="F&O EXCLUDED" variant="outlined" />
          ) : (
            <Chip size="small" color="warning" label="F&O INCLUDED" />
          )}
          {payload?.regimeEnforced ? (
            <Chip
              size="small"
              variant="outlined"
              label={payload?.gateOn ? 'GATE ON' : 'GATE OFF'}
            />
          ) : (
            <Chip size="small" color="warning" label="GATE NOT ENFORCED" />
          )}
        </Stack>

        <BtstFunnel funnel={payload?.funnel} />

        <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
          {payload?.note}
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          Currently eligible
          {payload?.slots ? (
            <Typography component="span" variant="body2" color="text.secondary">
              {' '}
              — the top {payload.slots} would be bought
            </Typography>
          ) : null}
        </Typography>

        {candidates.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            Nothing qualifies at the moment. That is the ordinary state — this
            strategy produces about one signal every two sessions — and the
            funnel above shows how far the universe got.
          </Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell align="right">#</TableCell>
                  <TableCell>Symbol</TableCell>
                  <TableCell align="right">Price</TableCell>
                  <TableCell align="right">
                    <Tooltip title="B5: volume so far against the 20-session average share volume. Measured on what has traded, never scaled up to a projected day.">
                      <span>Vol×</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="B6: where the price sits in today's range so far. 1.00 is at the high.">
                      <span>CLV</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="B4: how far above the prior 55-session high it is trading.">
                      <span>Above hi55</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">mom126</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {candidates.map((one) => (
                  <TableRow key={one.symbol}>
                    <TableCell align="right" className="numeric">
                      {one.rank}
                    </TableCell>
                    <TableCell>
                      {one.symbol}
                      {one.fnoEligible ? (
                        <Chip size="small" variant="outlined" label="F&O" sx={{ ml: 0.5 }} />
                      ) : null}
                    </TableCell>
                    <TableCell align="right" className="numeric">
                      {Number(one.price).toLocaleString('en-IN', {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </TableCell>
                    <TableCell align="right" className="numeric">
                      {Number(one.volRatio).toFixed(1)}×
                    </TableCell>
                    <TableCell align="right" className="numeric">
                      {Number(one.clv).toFixed(2)}
                    </TableCell>
                    <TableCell align="right" className="numeric">
                      {`+${(Number(one.aboveBreakout) * 100).toFixed(2)}%`}
                    </TableCell>
                    <TableCell align="right" className="numeric">
                      {`${Number(one.momentum) >= 0 ? '+' : ''}${(
                        Number(one.momentum) * 100
                      ).toFixed(1)}%`}
                    </TableCell>
                    <TableCell>
                      {/* Qualifying and being BOUGHT are different things:
                          only `slots` of them are. */}
                      {one.wouldTrade ? (
                        <Chip
                          size="small"
                          label="would trade"
                          sx={{
                            bgcolor: theme.market.upSoft,
                            color: theme.market.up,
                          }}
                        />
                      ) : (
                        <Chip size="small" variant="outlined" label="past the slots" />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {nearMisses.length ? (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            Near misses
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Names that broke out and then failed a later filter. Everything that
            fell at the first filter is in the funnel counts instead — a list of
            250 rows saying “did not break out” would hide these.
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mt: 1 }}>
            {nearMisses.map((one) => (
              <Tooltip key={one.symbol} title={one.reason}>
                <Chip size="small" variant="outlined" label={one.symbol} sx={{ mb: 0.5 }} />
              </Tooltip>
            ))}
          </Stack>
        </Paper>
      ) : null}

      {payload?.asOfIst ? (
        <Typography variant="caption" color="text.secondary">
          As of {new Date(payload.asOfIst).toLocaleTimeString()} — refreshed
          every {POLL_MS / 1000}s.
        </Typography>
      ) : null}
    </Stack>
  );
}
