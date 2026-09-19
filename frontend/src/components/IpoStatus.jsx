import { useEffect, useMemo, useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  Link as MuiLink,
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
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { formatCountdownLong } from '../utils/format';
import { chipTone } from '../theme/chipTone';

/**
 * The Status tab: is this feature doing its job, and what has it been doing.
 *
 * **ONE tab, not a Health tab and an Alerts tab.** /swing and /btst have two
 * because they trade unattended and the consequences are money; this sends a
 * message. Giving a non-strategy page a strategy page's shape by imitation is
 * how a page ends up with tabs that exist because the neighbours have them.
 *
 * The honesty rules from `frontend/CLAUDE.md` section 3, as they land here:
 *
 * - **"0 of 0 sweeps ran" is CORRECT on most days.** Nothing closes today on
 *   the great majority of days, so the panel says "no IPO closes today, no
 *   sweeps are scheduled" rather than rendering an empty schedule that reads
 *   as a fault.
 * - **A missed slot is only missed once its hour has fully passed.** The
 *   current hour may still be about to run, and calling it missed at 14:05
 *   would report a fault that is not one.
 * - **Failures are process-local and the panel says so.** A failed run
 *   deliberately leaves no row -- that is what lets it retry inside its own
 *   hour -- so the failure list empties on a restart. Silence there is not
 *   evidence of success.
 * - **Process figures are not restated.** The task table, CPU and the feed
 *   live on /health; this links to them.
 */

const IST = 'Asia/Kolkata';

function formatIst(iso) {
  if (!iso) return '—';
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '—';
  return when.toLocaleString('en-IN', {
    timeZone: IST,
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function Countdown({ iso, fallback }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (!iso) {
    return (
      <Typography variant="body2" color="text.secondary">
        {fallback}
      </Typography>
    );
  }
  const seconds = Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000));
  return (
    <Typography variant="body2">
      {formatIst(iso)}{' '}
      <Typography component="span" variant="caption" color="text.secondary">
        (in {formatCountdownLong(seconds)})
      </Typography>
    </Typography>
  );
}

function Figure({ label, children, hint }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      {children}
      {hint ? (
        <Typography variant="caption" color="text.secondary" display="block">
          {hint}
        </Typography>
      ) : null}
    </Box>
  );
}

function SlotStrip({ sweeps }) {
  const theme = useTheme();
  const completed = useMemo(() => new Set(sweeps.completedSlots), [sweeps]);
  const missed = useMemo(() => new Set(sweeps.missedSlots), [sweeps]);

  if (sweeps.closingToday === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No mainboard IPO closes today, so no reminder sweeps are scheduled.
        Nothing here is missing.
      </Typography>
    );
  }

  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {sweeps.expectedSlots.map((slot) => {
        const hour = slot.split('|')[1];
        const done = completed.has(slot);
        const gone = missed.has(slot);
        return (
          <Tooltip
            key={slot}
            title={
              done
                ? 'Sent, or correctly decided there was nothing outstanding'
                : gone
                  ? 'This hour passed with no record: the process was down, or every retry inside it failed'
                  : 'Not yet due'
            }
          >
            <Chip
              size="small"
              variant="outlined"
              label={`${hour}:00`}
              // Explicit colours: this theme gives every chip a background,
              // which turns `color="success"` into unreadable text on grey.
              sx={chipTone(theme, done ? 'done' : gone ? 'problem' : 'neutral')}
            />
          </Tooltip>
        );
      })}
    </Stack>
  );
}

export default function IpoStatus({ health, error, loading }) {
  const theme = useTheme();
  if (loading && !health) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">{error}</Alert>;
  }
  if (!health) return null;

  const { clock, sweeps, source, freshness, jobs, notes } = health;
  const clockOk = clock.enabled && clock.running;

  return (
    <Stack spacing={2}>
      {clock.enabled && !clock.running ? (
        <Alert severity="error">
          The IPO clock is switched on but its task is not running. Nothing will
          refresh and no reminder will be sent. The task is{' '}
          <code>{clock.taskName}</code> — see{' '}
          <MuiLink component={RouterLink} to="/health">
            System Health
          </MuiLink>
          .
        </Alert>
      ) : null}
      {!clock.enabled ? (
        <Alert severity="warning">
          The IPO clock is switched off (<code>ipo.scheduler_enabled</code>).
          The tabs and every action still work; nothing refreshes and no
          reminder is sent.
        </Alert>
      ) : null}
      {sweeps.missedSlots.length > 0 ? (
        <Alert severity="warning" icon={<WarningAmberIcon />}>
          {sweeps.missedSlots.length} reminder hour(s) passed today with no
          record: {sweeps.missedSlots.map((slot) => slot.split('|')[1]).join(', ')}
          :00. A slot is only catchable inside its own hour, so these will not
          be replayed.
        </Alert>
      ) : null}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
          The clock
        </Typography>
        <Grid container spacing={2}>
          <Grid item xs={12} sm={6} md={3}>
            <Figure label="State">
              <Chip
                size="small"
                variant="outlined"
                sx={chipTone(theme, clockOk ? 'done' : 'problem')}
                label={
                  clock.enabled
                    ? clock.running
                      ? 'Running'
                      : 'Enabled, task down'
                    : 'Switched off'
                }
              />
            </Figure>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Figure label="Next daily refresh" hint={`Scheduled ${clock.dailyRefreshAt} IST`}>
              <Countdown iso={clock.nextDailyRefreshAt} fallback="—" />
            </Figure>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Figure
              label="Next reminder sweep"
              hint={`Window ${clock.reminderFrom}–${clock.reminderTo} IST`}
            >
              <Countdown
                iso={clock.nextSweepAt}
                fallback="None scheduled — nothing closes today"
              />
            </Figure>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Figure label="Passes since start" hint={clock.lastError ? undefined : 'No errors'}>
              <Typography variant="body2">{clock.runs}</Typography>
              {clock.lastError ? (
                <Typography variant="caption" color="warning.main">
                  {clock.lastError}
                </Typography>
              ) : null}
            </Figure>
          </Grid>
        </Grid>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
          Today's reminder sweeps
        </Typography>
        <Stack spacing={1.5}>
          <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
            <Figure label="Closing today">
              <Typography variant="body2">{sweeps.closingToday}</Typography>
            </Figure>
            <Figure label="Still outstanding" hint="Not (applied and accepted), not rejected">
              <Typography
                variant="body2"
                color={sweeps.outstanding > 0 ? 'warning.main' : 'text.primary'}
              >
                {sweeps.outstanding}
              </Typography>
            </Figure>
            <Figure label="Sweeps completed">
              <Typography variant="body2">
                {sweeps.completedSlots.length}
                {sweeps.expectedSlots.length > 0
                  ? ` of ${sweeps.expectedSlots.length}`
                  : ''}
              </Typography>
            </Figure>
          </Stack>
          <SlotStrip sweeps={sweeps} />
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
          The source, and how fresh the GMP is
        </Typography>
        <Grid container spacing={2}>
          <Grid item xs={12} sm={6} md={3}>
            <Figure label="Host" hint="Named in one module; see the outbound-host test">
              <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
                {source.host}
              </Typography>
            </Figure>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Figure label="Last successful refresh" hint={source.lastRefreshDetail ?? undefined}>
              <Typography variant="body2">{formatIst(source.lastRefreshAt)}</Typography>
            </Figure>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Figure
              label="Tracked / stale readings"
              hint={
                freshness.staleCompanies.length > 0
                  ? freshness.staleCompanies.join(', ')
                  : 'Every reading is within its cadence'
              }
            >
              <Typography
                variant="body2"
                color={freshness.staleReadings > 0 ? 'warning.main' : 'text.primary'}
              >
                {freshness.trackedNotListed} / {freshness.staleReadings}
              </Typography>
            </Figure>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Figure label="Oldest capture" hint={`Newest ${formatIst(freshness.newestCaptureAt)}`}>
              <Typography variant="body2">
                {formatIst(freshness.oldestCaptureAt)}
              </Typography>
            </Figure>
          </Grid>
        </Grid>

        {source.recentFailures.length > 0 ? (
          <Box sx={{ mt: 2 }}>
            <Divider sx={{ mb: 1 }} />
            <Typography variant="caption" color="warning.main" display="block">
              Fetch failures since this process started (they are not stored, so
              this list empties on a restart):
            </Typography>
            {source.recentFailures.map((failure) => (
              <Typography key={`${failure.kind}-${failure.slotKey}-${failure.at}`} variant="caption" display="block">
                {formatIst(failure.at)} · {failure.kind} · {failure.detail}
              </Typography>
            ))}
          </Box>
        ) : null}
      </Paper>

      <Paper variant="outlined">
        <Box sx={{ p: 2, pb: 1 }}>
          <Typography variant="subtitle2">Job log</Typography>
          <Typography variant="caption" color="text.secondary">
            One row per completed slot. This is the record that answers "did the
            14:00 reminder actually go out".
          </Typography>
        </Box>
        {jobs.length === 0 ? (
          <Box sx={{ px: 2, pb: 2 }}>
            <Typography variant="body2" color="text.secondary">
              No job has completed yet.
            </Typography>
          </Box>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Job</TableCell>
                  <TableCell>Slot</TableCell>
                  <TableCell>Ran</TableCell>
                  <TableCell>What it did</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {jobs.map((job) => (
                  <TableRow key={`${job.kind}-${job.slotKey}`} hover>
                    <TableCell>
                      {job.kind === 'CLOSING_SWEEP' ? 'Reminder sweep' : 'Daily refresh'}
                    </TableCell>
                    <TableCell>{job.slotKey}</TableCell>
                    <TableCell>{formatIst(job.startedAt)}</TableCell>
                    <TableCell>{job.detail ?? '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Box>
        {notes.map((note) => (
          <Typography key={note} variant="caption" color="text.secondary" display="block">
            · {note}
          </Typography>
        ))}
      </Box>
    </Stack>
  );
}
