import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Divider,
  Link as MuiLink,
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
import { Link as RouterLink } from 'react-router-dom';
import { useTheme } from '@mui/material/styles';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { btstApi } from '../api/btst';

/**
 * Is this strategy healthy, and DID THE EXIT RUN?
 *
 * The per-strategy sibling of the system health page, and admin-only for the
 * same reason the rotation's health tab is: it serves live machinery state and
 * WARNING+ log records, which is the exposure `/api/healthcheck/*` is gated
 * for. The tab is not offered to a ROLE_USER, and that is presentation — the
 * endpoint refuses them with a 403 regardless.
 *
 * **"Did the exit run" is the FIRST panel, before the clock and before the
 * bars.** For every other strategy in this application a missed run costs a
 * session's decision and is reported; for this one it costs the trade thesis.
 *
 * **"Off" is not "broken".** `Verdict` takes three tones rather than a
 * boolean: a switched-off strategy is working exactly as configured, and
 * colouring it red would make the panel cry wolf on the state an operator
 * chose. Red is reserved for something that should be running and is not.
 */

const POLL_MS = 10000;

export default function BtstHealth({ strategyKey, portfolioId }) {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setPayload(await btstApi.health(strategyKey, portfolioId));
      setError(null);
    } catch (caught) {
      setError(caught.message || 'Could not read the health payload');
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

  if (error) return <Alert severity="error">{error}</Alert>;

  const exit = payload?.exit || {};
  const working = payload?.working || {};
  const feed = payload?.feed || {};
  const data = payload?.data || {};

  return (
    <Stack spacing={2}>
      {/* FIRST, deliberately. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Verdict tone={exit.tone} title="Did the exit run?" text={exit.verdict} />
        <Stack direction="row" spacing={3} sx={{ mt: 1.5 }} flexWrap="wrap">
          <Figure label="Held now" value={exit.openCount} />
          <Figure label="Overdue" value={exit.overdueCount} bad={exit.overdueCount > 0} />
          <Figure
            label="Late exits, ever"
            value={exit.lateExitsEver}
            bad={exit.lateExitsEver > 0}
          />
          <Figure label="Failed, ever" value={exit.failedEver} bad={exit.failedEver > 0} />
          <Figure label="Exit time" value={`${exit.exitAtIst} IST`} />
        </Stack>
        {exit.overdue?.length ? (
          <TableContainer sx={{ mt: 1.5 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Symbol</TableCell>
                  <TableCell align="right">Qty</TableCell>
                  <TableCell>Entered</TableCell>
                  <TableCell>Why it is still held</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {exit.overdue.map((one) => (
                  <TableRow key={one.symbol}>
                    <TableCell>{one.symbol}</TableCell>
                    <TableCell align="right" className="numeric">
                      {one.quantity}
                    </TableCell>
                    <TableCell>{one.entrySessionDate}</TableCell>
                    <TableCell>
                      <Typography variant="caption">
                        {one.exitReason || one.exitStatus}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        ) : null}
        {exit.lastExitRun ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
            Last exit run: {exit.lastExitRun.sessionDate} — {exit.lastExitRun.message}
          </Typography>
        ) : (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
            The exit has never run.
          </Typography>
        )}
      </Paper>

      {/* The second panel the handoff singled out. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Verdict tone={feed.tone} title="Is the universe subscribed?" text={feed.verdict} />
        <Stack direction="row" spacing={3} sx={{ mt: 1.5 }} flexWrap="wrap">
          <Figure label="Instruments on the feed" value={feed.instrumentCount ?? 'none'} />
          <Figure label="Universe" value={feed.universeSize} />
          <Figure
            label="Window"
            value={`${feed.windowOpensAtIst}–${feed.windowClosesAtIst} IST`}
          />
          <Figure label="Open now" value={feed.windowOpen ? 'yes' : 'no'} />
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          {feed.note}
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1">Background work</Typography>
        <Stack direction="row" spacing={3} sx={{ mt: 1 }} flexWrap="wrap">
          <Figure label="Enabled" value={working.enabled ? 'yes' : 'no'} />
          <Figure label="Armed" value={working.armed ? 'yes' : 'no'} />
          <Figure label="Market" value={working.market?.open ? 'open' : 'closed'} />
        </Stack>
        <TableContainer sx={{ mt: 1 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Task</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>State</TableCell>
                <TableCell>Expected</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(working.jobs?.rows || []).map((one) => (
                <TableRow key={one.name}>
                  <TableCell>{one.name}</TableCell>
                  <TableCell>{one.role}</TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={one.state}
                      color={
                        one.expected && one.state !== 'running' ? 'error' : 'default'
                      }
                    />
                  </TableCell>
                  <TableCell>{one.expected ? 'yes' : 'no'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        {/* The ABSENCE of a stop watcher is the point rather than an
            omission, so it is stated rather than left as a blank row. */}
        {working.stopWatcher ? (
          <Alert severity="info" icon={<InfoOutlinedIcon />} sx={{ mt: 1.5 }}>
            {working.stopWatcher.note}
          </Alert>
        ) : null}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Verdict tone={data.tone} title="Are the bars fresh enough?" text={data.verdict} />
        <Stack direction="row" spacing={3} sx={{ mt: 1.5 }} flexWrap="wrap">
          <Figure label="Newest session" value={data.newestSession || 'none'} />
          <Figure
            label="Symbols with bars"
            value={`${data.symbolsWithBars} of ${data.universeSize}`}
          />
          <Figure label="Staleness limit" value={`${data.stalenessLimitDays} days`} />
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          {data.note}
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          Recent problems
        </Typography>
        {payload?.problems?.available === false ? (
          <Typography variant="body2" color="text.secondary">
            {payload.problems.note}
          </Typography>
        ) : (payload?.problems?.records || []).length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            Nothing has been logged at WARNING or above.
          </Typography>
        ) : (
          <Stack spacing={0.5}>
            {payload.problems.records.slice(0, 30).map((one, index) => (
              <Typography key={index} variant="caption" sx={{ fontFamily: 'monospace' }}>
                [{one.level}] {one.logger}: {one.message}
              </Typography>
            ))}
          </Stack>
        )}
        {payload?.problems?.bufferNote ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            {payload.problems.bufferNote}
          </Typography>
        ) : null}
      </Paper>

      {/* What this tab cannot know, said rather than implied away. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          What this tab cannot tell you
        </Typography>
        <Stack spacing={0.5}>
          {(payload?.notes || []).map((one, index) => (
            <Typography key={index} variant="caption" color="text.secondary">
              • {one}
            </Typography>
          ))}
        </Stack>
        <Divider sx={{ my: 1.5 }} />
        <Typography variant="caption" color="text.secondary">
          Figures about the PROCESS rather than this strategy — the upstream
          feed, CPU, the task table — are on the{' '}
          <MuiLink component={RouterLink} to="/health">
            system health page
          </MuiLink>
          .
        </Typography>
      </Paper>
    </Stack>
  );
}

/** Three tones, because two would lie: `off` is not `bad`. */
function Verdict({ tone, title, text }) {
  const theme = useTheme();
  const colour =
    tone === 'bad'
      ? theme.market.down
      : tone === 'off'
        ? theme.palette.text.secondary
        : theme.market.up;
  return (
    <Box>
      <Typography variant="subtitle1">{title}</Typography>
      <Typography variant="body2" sx={{ color: colour, fontWeight: 500 }}>
        {text}
      </Typography>
    </Box>
  );
}

function Figure({ label, value, bad }) {
  const theme = useTheme();
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body1"
        className="numeric"
        sx={{ color: bad ? theme.market.down : 'inherit', fontWeight: bad ? 600 : 400 }}
      >
        {value ?? '—'}
      </Typography>
    </Box>
  );
}
