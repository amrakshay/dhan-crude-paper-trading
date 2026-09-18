import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  Grid,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { swingApi } from '../api/swing';
import SwingExplainer from '../components/SwingExplainer';
import { useAuth } from '../auth/AuthContext';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { formatCountdownLong, formatPrice, formatQty } from '../utils/format';

const STRATEGY = 'nse-swing-momentum';

/**
 * NSE Swing Momentum.
 *
 * The decision journal of the only strategy in this application that trades
 * with nobody watching, so `frontend/CLAUDE.md` section 3's honesty rules
 * apply harder here than anywhere except the health page:
 *
 * - **Undefined is never zero.** A breadth that could not be measured renders
 *   as "not measured", not as 0%. A slot count of null is not zero slots. A
 *   withheld equity figure says why it was withheld.
 * - **Enabled and ARMED are shown as two states**, because they are two
 *   switches and the difference is whether software may spend money.
 * - **There is no live track record**, and the page says so above every
 *   performance figure. The specification's 19.9% is a survivorship-biased,
 *   in-sample backtest of a rule that has never traded a rupee.
 *
 * The page polls at 10 s. Nothing on it moves faster: the ranking changes once
 * a session and the stops are recomputed once a night. Prices come from the
 * server's own marks rather than the socket, because this page is a record
 * rather than a quote screen.
 */

const POLL_MS = 10000;

function Missing({ children = 'not measured', hint }) {
  const body = (
    <Typography variant="body2" color="text.disabled" component="span">
      {children}
    </Typography>
  );
  return hint ? <Tooltip title={hint}>{body}</Tooltip> : body;
}

function Figure({ label, value, hint, tone }) {
  const theme = useTheme();
  const colour =
    tone === 'up' ? theme.market.up : tone === 'down' ? theme.market.down : undefined;
  return (
    <Stack spacing={0.25}>
      <Stack direction="row" spacing={0.5} alignItems="center">
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
        {hint ? (
          <Tooltip title={hint}>
            <InfoOutlinedIcon sx={{ fontSize: 12, color: 'text.disabled' }} />
          </Tooltip>
        ) : null}
      </Stack>
      <Typography
        variant="h6"
        className="numeric"
        sx={{ fontWeight: 600, color: colour }}
      >
        {value}
      </Typography>
    </Stack>
  );
}

/**
 * Before the first poll lands we know NOTHING — which is not the same as "off",
 * "closed" or "zero". Rendering the not-yet-loaded state as a switched-off
 * strategy in a closed market is exactly the failure §3 is about: it is a
 * confident answer to a question nobody has asked the server yet.
 */
function NotLoadedYet({ children = 'Loading…' }) {
  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack direction="row" spacing={1.5} alignItems="center">
        <CircularProgress size={16} />
        <Typography variant="body2" color="text.secondary">
          {children}
        </Typography>
      </Stack>
    </Paper>
  );
}

function percent(value, digits = 1) {
  if (value === null || value === undefined) return null;
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function GateCard({ status }) {
  const snapshot = status?.snapshot;
  const regime = snapshot?.regime;
  const gate = snapshot?.effectiveGate;

  if (!status) {
    return <NotLoadedYet>Reading the regime, the breadth and the slots…</NotLoadedYet>;
  }
  if (!status?.enabled) {
    return (
      <Alert severity="info">
        The strategy is switched off. It decides nothing and records nothing
        while it is off — and its history below is unchanged, which is exactly
        what a journal is for.
      </Alert>
    );
  }
  if (status?.snapshotError) {
    return (
      <Alert severity="warning" icon={<WarningAmberIcon />}>
        The live snapshot could not be computed: {status.snapshotError}
      </Alert>
    );
  }
  if (!snapshot) return null;

  const gateOn = regime?.gateOn;
  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="baseline"
        flexWrap="wrap"
        gap={1}
      >
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Typography variant="h5" sx={{ fontWeight: 600 }}>
            {gateOn ? 'Regime gate ON' : 'Regime gate OFF'}
          </Typography>
          {/* A gate that looks on while nothing acts on it is the one thing
              this card must never show. */}
          {gate?.enforceRegime === false ? (
            <Chip
              size="small"
              label="NOT ENFORCED"
              sx={{ bgcolor: 'warning.main', color: 'warning.contrastText', fontWeight: 600 }}
            />
          ) : null}
        </Stack>
        <Typography variant="caption" color="text.secondary">
          session {snapshot.asOf}
        </Typography>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
        {regime?.reason}
      </Typography>

      <Divider sx={{ my: 2 }} />

      <Grid container spacing={2}>
        <Grid item xs={6} sm={4} md={2}>
          <Figure
            label={`${regime?.indexSymbol ?? 'Index'} close`}
            value={regime?.close !== null && regime?.close !== undefined
              ? formatPrice(regime.close)
              : <Missing />}
          />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <Figure
            label="200-session SMA"
            value={regime?.sma200 !== null && regime?.sma200 !== undefined
              ? formatPrice(regime.sma200)
              : <Missing hint="Fewer stored sessions than the SMA needs. The gate is treated as OFF rather than assumed ON." />}
          />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <Figure
            label="Breadth"
            hint="The fraction of the liquid universe trading above its own 200-session SMA. Null means it could not be measured, which is not the same as zero."
            value={
              snapshot.breadthPercent === null || snapshot.breadthPercent === undefined ? (
                <Missing />
              ) : (
                `${snapshot.breadthPercent}%`
              )
            }
          />
          <Typography variant="caption" color="text.disabled">
            {snapshot.aboveOwnSma200} of {snapshot.liquidUniverse}
          </Typography>
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <Figure
            label="Slots allowed"
            hint="round(10 × clamp((breadth − 0.35) / 0.30, 0, 1)). 35% of the liquid universe buys nothing; 65% buys the full book."
            value={
              gate?.slots === null || gate?.slots === undefined ? (
                <Missing />
              ) : (
                `${gate.slots} of ${snapshot.parameters?.maxPositions ?? 10}`
              )
            }
          />
          {/* The EFFECTIVE count above, the breadth ramp's own answer below.
              They differ whenever the regime gate overrides the ramp, and
              hiding the ramp's figure would lose the number an operator
              checks against the specification. */}
          {snapshot.slots !== null &&
          snapshot.slots !== undefined &&
          gate?.slots !== snapshot.slots ? (
            <Typography variant="caption" color="text.disabled">
              breadth ramp allows {snapshot.slots}; the gate overrides it
            </Typography>
          ) : null}
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <Figure
            label="Entries"
            value={
              gate?.entriesAllowed ? 'allowed' : 'blocked'
            }
            tone={gate?.entriesAllowed ? 'up' : 'down'}
            hint={
              gate?.enforceEntryReturn === false
                ? 'The 63-session entry filter is NOT being enforced, so it blocks nothing. It never forced an exit either way.'
                : 'The 63-session index return has to be above zero for a NEW entry. It never forces an exit.'
            }
          />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <Figure
            label="Candidates"
            value={formatQty(snapshot.candidateCount)}
            hint={`${snapshot.symbolsWithBars} of ${snapshot.universeSize} universe symbols have stored bars.`}
          />
        </Grid>
      </Grid>

      {gate?.relaxed ? (
        <Alert severity="warning" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {gate.enforceRegime === false && gate.enforceEntryReturn === false
              ? 'The regime gate and the 63-session entry filter are NOT being enforced.'
              : gate.enforceRegime === false
                ? 'The regime gate is NOT being enforced.'
                : 'The 63-session entry filter is NOT being enforced.'}
          </Typography>
          <Typography variant="body2">
            The gate is still computed and still recorded — on this session, on
            every decision row and on every position opened under it — so these
            trades can be filtered out of the numbers later. Nothing else
            changes: breadth still sizes the book, the momentum floor still
            applies, the rotation exit still fires and the trailing stop is
            untouched.
          </Typography>
          <Typography variant="caption" color="text.secondary">
            This is a deliberate divergence from the specification, whose own
            research tested thirteen ways of trading while the gate is off and
            found none that beat holding cash. Switch it back on under Rules on
            Strategies &amp; Features — that stops new entries and does NOT sell
            what is already open.
          </Typography>
        </Alert>
      ) : null}

      {status?.policyContradiction ? (
        <Alert severity="error" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          {status.policyContradiction}
        </Alert>
      ) : null}

      {gate?.variant === 'v3b-off-gate' ? (
        <Alert severity="warning" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          The V3b off-gate variant is ENABLED. It holds up to {gate.slots}{' '}
          position(s) while the index is below its 200-day SMA, at a{' '}
          {percent(gate.momentumFloor, 0)} momentum floor, and the regime exit
          no longer liquidates the book. The owner&apos;s own research tested
          thirteen variants and not one beat holding cash: V3b&apos;s trades are
          good in isolation and its total is still lower, because capital
          committed to a bear rally is not available at the regime flip.
        </Alert>
      ) : null}
    </Paper>
  );
}

/**
 * WHAT THE STRATEGY IS DOING RIGHT NOW.
 *
 * The rest of this page is a record of what it DECIDED. This is the other
 * question — what it is doing, what it will do next and when, and what it last
 * did — and it is the one an operator actually has at 09:16.
 *
 * The countdown ticks LOCALLY off the absolute timestamp the server sends
 * (the Settings-page trick, frontend/CLAUDE.md §4) rather than re-fetching
 * every second, so a stalled poll shows up as a clock that keeps running past
 * a run that never happened.
 *
 * §3's honesty rules apply hard here:
 *  - a countdown that cannot be computed says so; it never shows 00:00;
 *  - "the scheduler is not running", "idle" and "watching nothing" are three
 *    different states and read differently;
 *  - a strategy that is enabled but NOT ARMED says, here, that it will decide
 *    and place nothing.
 */
function ActivityStrip({ status }) {
  // Hooks must run unconditionally, so the "not loaded" branch lives in the
  // body rather than in front of it.
  const theme = useTheme();
  const scheduler = status?.scheduler ?? {};
  const monitor = status?.stopMonitor ?? {};
  const schedule = (scheduler.schedules ?? []).find(
    (one) => one.strategyKey === status?.strategyKey,
  );
  const last = (scheduler.recent ?? [])[0];

  // One local tick a second, so the countdowns move between polls.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const countdown = (iso) => {
    if (!iso) return null;
    const target = Date.parse(iso);
    if (Number.isNaN(target)) return null;
    return formatCountdownLong((target - now) / 1000);
  };

  const nextRebalance = countdown(schedule?.nextRebalanceAtIst);
  const nextNightly = countdown(schedule?.nextNightlyAtIst);

  if (!status) {
    return (
      <NotLoadedYet>
        Reading what the strategy is doing…
      </NotLoadedYet>
    );
  }

  const activity = scheduler.running === false
    ? 'the scheduler is not running'
    : scheduler.activity ?? 'idle — waiting for the next scheduled run';

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        useFlexGap
      >
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Chip
            size="small"
            label={status?.marketOpen ? 'market OPEN' : 'market closed'}
            sx={{
              bgcolor: status?.marketOpen ? theme.market.upSoft : 'action.selected',
              color: status?.marketOpen ? theme.market.up : 'text.secondary',
              fontWeight: 600,
            }}
          />
          <Typography variant="body2" className="numeric" color="text.secondary">
            {scheduler.nowIst
              ? `${new Date(scheduler.nowIst).toLocaleTimeString()} IST`
              : 'clock unavailable'}
          </Typography>
          {schedule ? (
            <Typography variant="caption" color="text.disabled">
              trades {schedule.marketOpensAtIst}–{schedule.marketClosesAtIst} IST
            </Typography>
          ) : null}
        </Stack>

        <Stack direction="row" spacing={1} alignItems="center">
          {scheduler.activity ? <CircularProgress size={14} /> : null}
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {activity}
          </Typography>
        </Stack>
      </Stack>

      {/* An enabled strategy that is not armed decides and places nothing.
          Said here, in the strip, because this is the panel somebody looks at
          when they are wondering why no order appeared. */}
      {status?.enabled && !status?.armed ? (
        <Alert severity="info" icon={<InfoOutlinedIcon />} sx={{ mt: 2 }}>
          NOT ARMED. It will compute, decide and write a decision record at every
          scheduled run, and it will place no order at all.
        </Alert>
      ) : null}
      {!status?.enabled ? (
        <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mt: 2 }}>
          Switched OFF. Nothing is scheduled, nothing is decided and no trailing
          stop is recomputed. The record below is unchanged.
        </Alert>
      ) : null}

      <Divider sx={{ my: 2 }} />

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} md={3}>
          <Figure
            label="Next rebalance"
            value={
              nextRebalance
                ? `in ${nextRebalance}`
                : schedule
                  ? 'not scheduled'
                  : '—'
            }
            hint={
              schedule?.nextRebalanceAtIst
                ? `${new Date(schedule.nextRebalanceAtIst).toLocaleString()} IST. Weekday only — this application has no holiday list, because the trading calendar is the index's own bar dates. On an exchange holiday the countdown runs down and the run records a skipped session.`
                : 'No next run could be computed.'
            }
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Figure
            label="Next nightly"
            value={nextNightly ? `in ${nextNightly}` : schedule ? 'not scheduled' : '—'}
            hint={
              schedule?.nextNightlyAtIst
                ? `${new Date(schedule.nextNightlyAtIst).toLocaleString()} IST. It refreshes the daily bars, decides, ratchets every trailing stop and journals. It places no order.`
                : 'No next run could be computed.'
            }
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Figure
            label="Last run"
            value={
              last
                ? `${last.kind.toLowerCase()} ${last.ok ? 'ok' : 'FAILED'}`
                : 'none this session'
            }
            tone={last && !last.ok ? 'down' : undefined}
            hint={
              last
                ? `${new Date(last.atIst).toLocaleString()} — ${last.detail || 'no detail'}`
                : 'This process has run no scheduled job since it started. That is not the same as none having happened: the record below is the history.'
            }
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Figure
            label="Stops watched"
            value={
              monitor.enabled === false
                ? 'monitor off'
                : monitor.running === false
                  ? 'not running'
                  : monitor.watching === null || monitor.watching === undefined
                    ? 'no pass yet'
                    : monitor.watching
            }
            tone={
              monitor.enabled === false || monitor.running === false
                ? 'down'
                : undefined
            }
            hint="Active chandelier stops with a level to compare against. A monitor that is not running, one that has not completed a pass, and one watching nothing are three different states."
          />
        </Grid>
      </Grid>

      <Stack
        direction="row"
        spacing={2}
        sx={{ mt: 2 }}
        flexWrap="wrap"
        useFlexGap
      >
        <Typography variant="caption" color="text.secondary">
          Nearest stop:{' '}
          {monitor.nearestPercent === null || monitor.nearestPercent === undefined ? (
            <Missing hint="Nothing measurable: no stop is being watched, or no watched position has a live mark. A book with no marks does not have a nearest stop of 0%." />
          ) : (
            <span className="numeric">
              {monitor.nearestSymbol} {monitor.nearestPercent.toFixed(2)}% above its
              level
            </span>
          )}
        </Typography>
        {monitor.unprotected ? (
          <Typography variant="caption" color="warning.main">
            {monitor.unprotected} position(s) have no stop yet — ATR14 was not
            available at entry, and the next nightly ratchet sets one.
          </Typography>
        ) : null}
        {monitor.deferredToAuction ? (
          <Typography variant="caption" color="warning.main">
            {monitor.deferredToAuction} exit(s) deferred to the next open by the
            closing auction.
          </Typography>
        ) : null}
        {monitor.lastPassAtIst ? (
          <Typography variant="caption" color="text.disabled">
            last pass {new Date(monitor.lastPassAtIst).toLocaleTimeString()}
          </Typography>
        ) : (
          <Typography variant="caption" color="text.disabled">
            the monitor has completed no pass since this process started
          </Typography>
        )}
        {monitor.error ? (
          <Typography variant="caption" color="error.main">
            monitor error: {monitor.error}
          </Typography>
        ) : null}
      </Stack>
    </Paper>
  );
}

/**
 * The last twenty scheduled jobs, newest first.
 *
 * A STATUS DICT, not a second journal: it lives in the process and starts empty
 * after a restart. The real record is `swing_sessions`, below.
 */
function ActivityLog({ status }) {
  const rows = status?.scheduler?.recent ?? [];
  if (!rows.length) {
    return (
      <Alert severity="info">
        No scheduled job has run since this process started. That is not the same
        as none having happened — this list lives in memory, and the decision
        history below is the record.
      </Alert>
    );
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>At (IST)</TableCell>
            <TableCell>Job</TableCell>
            <TableCell>Result</TableCell>
            <TableCell>Detail</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((run, index) => (
            <TableRow key={`${run.kind}-${run.atIst}-${index}`}>
              <TableCell className="numeric">
                {new Date(run.atIst).toLocaleString()}
              </TableCell>
              <TableCell>{run.kind}</TableCell>
              <TableCell>
                <Chip
                  size="small"
                  label={run.ok ? 'ok' : 'FAILED'}
                  sx={{
                    bgcolor: run.ok ? 'action.selected' : 'error.main',
                    color: run.ok ? 'text.secondary' : 'error.contrastText',
                  }}
                />
              </TableCell>
              <TableCell>
                <Typography variant="caption" color="text.secondary">
                  {run.detail || '—'}
                </Typography>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function ArmingCard({ status }) {
  if (!status) {
    return <NotLoadedYet>Reading the schedule…</NotLoadedYet>;
  }
  return <ArmingCardBody status={status} />;
}

function ArmingCardBody({ status }) {
  const scheduler = status?.scheduler ?? {};
  const schedule = (scheduler.schedules ?? []).find(
    (one) => one.strategyKey === status?.strategyKey,
  );
  const missed = scheduler.missedRunCount ?? 0;

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Chip
          size="small"
          label={status?.enabled ? 'ENABLED' : 'OFF'}
          sx={{
            bgcolor: status?.enabled ? 'success.main' : 'action.selected',
            color: status?.enabled ? 'success.contrastText' : 'text.secondary',
          }}
        />
        <Chip
          size="small"
          label={status?.armed ? 'ARMED' : 'NOT ARMED'}
          sx={{
            bgcolor: status?.armed ? 'warning.main' : 'action.selected',
            color: status?.armed ? 'warning.contrastText' : 'text.secondary',
          }}
        />
        <Chip
          size="small"
          label={status?.marketOpen ? 'market open' : 'market closed'}
          sx={{ bgcolor: 'action.selected', color: 'text.secondary' }}
        />
      </Stack>

      <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
        {status?.armed
          ? 'Armed: this module submits its own orders on its own schedule. Every one is paper money in this database and nothing reaches a broker.'
          : 'Not armed: it computes, decides and writes a decision record every session, and places nothing. Arming is a separate switch on Strategies & Features.'}
      </Typography>

      <Divider sx={{ my: 2 }} />

      <Grid container spacing={2}>
        <Grid item xs={6} sm={3}>
          <Figure label="Nightly" value={schedule?.nightlyAtIst ?? '—'} hint="IST. After the close, once Dhan's end-of-day data has settled." />
        </Grid>
        <Grid item xs={6} sm={3}>
          <Figure label="Rebalance" value={schedule?.rebalanceAtIst ?? '—'} hint="IST. Just after the open: the rule executes at the next session's open." />
        </Grid>
        <Grid item xs={6} sm={3}>
          <Figure label="Cadence" value={schedule?.cadence ?? '—'} />
        </Grid>
        <Grid item xs={6} sm={3}>
          <Figure
            label="Missed runs"
            value={missed}
            tone={missed ? 'down' : undefined}
            hint="Sessions with no decision record. Reported, never silently re-decided: a decision recorded days late, on bars that may since have been restated, would be a record of a decision nobody took."
          />
        </Grid>
      </Grid>

      {missed > 0 ? (
        <Alert severity="warning" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          {(scheduler.missedRuns ?? []).map((entry) => (
            <Typography key={`${entry.kind}-${entry.sessions.join()}`} variant="body2">
              {entry.count} missed {entry.kind} run(s): {entry.sessions.join(', ')}.
              Trailing stops were not recomputed on those sessions.
            </Typography>
          ))}
        </Alert>
      ) : null}

      {status?.closingAuction ? (
        <Typography variant="caption" color="text.disabled" sx={{ mt: 2, display: 'block' }}>
          {status.closingAuction.note}
        </Typography>
      ) : null}
    </Paper>
  );
}

function BookTable({ book }) {
  const theme = useTheme();
  const rows = book?.positions ?? [];

  if (!rows.length) {
    return (
      <Alert severity="info">
        Nothing held. {book?.rankedAsOf
          ? `Last ranked on ${book.rankedAsOf}.`
          : 'No decision has been recorded yet.'}
      </Alert>
    );
  }

  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Symbol</TableCell>
            <TableCell align="right">Qty</TableCell>
            <TableCell align="right">Entry</TableCell>
            <TableCell align="right">Mark</TableCell>
            <TableCell align="right">Unrealised</TableCell>
            <TableCell align="right">Rank</TableCell>
            <TableCell align="right">Stop</TableCell>
            <TableCell align="right">Distance</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => {
            const stop = row.stop;
            const unrealised =
              row.unrealised === null || row.unrealised === undefined
                ? null
                : Number(row.unrealised);
            const rotating =
              row.rank !== null && row.rank !== undefined && row.rank > row.rotationExitRank;
            return (
              <TableRow key={row.securityId} hover>
                <TableCell sx={{ fontWeight: 500 }}>{row.symbol}</TableCell>
                <TableCell align="right" className="numeric">
                  {formatQty(row.quantity)}
                </TableCell>
                <TableCell align="right" className="numeric">
                  {formatPrice(Number(row.averagePrice))}
                </TableCell>
                <TableCell align="right" className="numeric">
                  {row.mark === null ? <Missing>no mark</Missing> : formatPrice(Number(row.mark))}
                </TableCell>
                <TableCell align="right" className="numeric">
                  {unrealised === null ? (
                    <Missing hint="No live price for this name, so mark-to-market is unknown. It is not zero.">
                      no mark
                    </Missing>
                  ) : (
                    <Typography
                      variant="body2"
                      className="numeric"
                      sx={{
                        color: unrealised >= 0 ? theme.market.up : theme.market.down,
                        fontWeight: 500,
                      }}
                    >
                      {unrealised >= 0 ? '+' : ''}
                      {formatPrice(unrealised)}
                    </Typography>
                  )}
                </TableCell>
                <TableCell align="right" className="numeric">
                  {row.rank === null || row.rank === undefined ? (
                    <Tooltip title="Not ranked on the last session — a rotation exit is due.">
                      <Typography variant="body2" sx={{ color: theme.market.down }}>
                        unranked
                      </Typography>
                    </Tooltip>
                  ) : (
                    <Typography
                      variant="body2"
                      className="numeric"
                      sx={{ color: rotating ? theme.market.down : undefined }}
                    >
                      {row.rank}
                    </Typography>
                  )}
                </TableCell>
                <TableCell align="right" className="numeric">
                  {stop ? (
                    <Tooltip
                      title={`Highest close since entry ${stop.highestClose} − ${stop.atrMultiple} × ATR14 ${stop.lastAtr ?? stop.entryAtr}. It never ratchets down.`}
                    >
                      <span>{formatPrice(Number(stop.stopPrice))}</span>
                    </Tooltip>
                  ) : (
                    <Missing hint="No ATR was available at entry, so no chandelier stop was set. The next nightly ratchet sets one.">
                      no stop
                    </Missing>
                  )}
                </TableCell>
                <TableCell align="right" className="numeric">
                  {stop && stop.distancePercent !== null && stop.distancePercent !== undefined ? (
                    <Typography variant="body2" className="numeric">
                      {stop.distancePercent.toFixed(1)}%
                    </Typography>
                  ) : (
                    <Missing>—</Missing>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function RankingTable({ snapshot }) {
  const rows = snapshot?.ranking ?? [];
  if (!rows.length) {
    return <Alert severity="info">No name passed the filters on this session.</Alert>;
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell align="right">#</TableCell>
            <TableCell>Symbol</TableCell>
            <TableCell align="right">Score</TableCell>
            <TableCell align="right">Momentum</TableCell>
            <TableCell align="right">ATR%</TableCell>
            <TableCell align="right">Close</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.symbol} hover>
              <TableCell align="right" className="numeric">
                {row.rank}
              </TableCell>
              <TableCell sx={{ fontWeight: 500 }}>{row.symbol}</TableCell>
              <TableCell align="right" className="numeric">
                {Number(row.score).toFixed(1)}
              </TableCell>
              <TableCell align="right" className="numeric">
                {percent(row.momentum)}
              </TableCell>
              <TableCell align="right" className="numeric">
                {percent(row.atrPercent, 2)}
              </TableCell>
              <TableCell align="right" className="numeric">
                {formatPrice(row.close)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function SessionRow({ session, onOpen, open, detail }) {
  return (
    <>
      <TableRow hover sx={{ cursor: 'pointer' }} onClick={() => onOpen(session.id)}>
        <TableCell>{session.sessionDate}</TableCell>
        <TableCell>{session.runKind}</TableCell>
        <TableCell>{session.status}</TableCell>
        <TableCell align="right" className="numeric">
          {session.gateOn === null || session.gateOn === undefined
            ? '—'
            : session.gateOn
              ? 'ON'
              : 'OFF'}
        </TableCell>
        <TableCell align="right" className="numeric">
          {session.breadth === null || session.breadth === undefined
            ? '—'
            : `${(session.breadth * 100).toFixed(1)}%`}
        </TableCell>
        <TableCell align="right" className="numeric">
          {session.slots === null || session.slots === undefined ? '—' : session.slots}
        </TableCell>
        <TableCell sx={{ maxWidth: 420 }}>
          <Typography variant="caption" color="text.secondary" noWrap>
            {session.message}
          </Typography>
        </TableCell>
        <TableCell align="right">
          <ExpandMoreIcon
            sx={{
              fontSize: 18,
              color: 'text.disabled',
              transform: open ? 'rotate(180deg)' : 'none',
              transition: 'transform 120ms',
            }}
          />
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={8} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ py: 1.5 }}>
              {!detail ? (
                <CircularProgress size={16} />
              ) : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Symbol</TableCell>
                      <TableCell>Action</TableCell>
                      <TableCell align="right">Rank</TableCell>
                      <TableCell align="right">Qty</TableCell>
                      <TableCell align="right">Order</TableCell>
                      <TableCell>Reason</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(detail.decisions ?? []).map((decision, index) => (
                      <TableRow key={`${decision.symbol}-${index}`}>
                        <TableCell>{decision.symbol}</TableCell>
                        <TableCell>{decision.action}</TableCell>
                        <TableCell align="right" className="numeric">
                          {decision.rank ?? '—'}
                        </TableCell>
                        <TableCell align="right" className="numeric">
                          {decision.quantity ?? '—'}
                        </TableCell>
                        <TableCell align="right" className="numeric">
                          {decision.orderId ?? (
                            <Tooltip title="No order: either this decision produced no trade, or the strategy was not armed.">
                              <span>—</span>
                            </Tooltip>
                          )}
                        </TableCell>
                        <TableCell>
                          <Typography variant="caption" color="text.secondary">
                            {decision.reason}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

function PerformanceCard({ performance }) {
  const theme = useTheme();
  if (!performance) return null;
  const mix = performance.exitMix ?? {};

  return (
    <Stack spacing={2}>
      <Alert severity="info" icon={<InfoOutlinedIcon />}>
        {performance.trackRecord?.note}
      </Alert>

      <Paper variant="outlined" sx={{ p: 2.5 }}>
        <Grid container spacing={2}>
          <Grid item xs={6} sm={4} md={2}>
            <Figure label="Trades" value={formatQty(performance.trades)} />
          </Grid>
          <Grid item xs={6} sm={4} md={2}>
            <Figure
              label="Win rate"
              value={performance.winRate === null ? <Missing /> : percent(performance.winRate)}
            />
          </Grid>
          <Grid item xs={6} sm={4} md={2}>
            <Figure
              label="Profit factor"
              value={
                performance.profitFactor === null ? (
                  <Missing hint="Undefined while nothing has lost money: dividing by a gross loss of zero." />
                ) : (
                  performance.profitFactor.toFixed(2)
                )
              }
            />
          </Grid>
          <Grid item xs={6} sm={4} md={2}>
            <Figure
              label="Avg hold"
              value={
                performance.averageHoldSessions === null ||
                performance.averageHoldSessions === undefined ? (
                  <Missing />
                ) : (
                  `${performance.averageHoldSessions} sessions`
                )
              }
              hint="Measured in sessions, which is what the specification reports (20.1 for the backtest). Calendar days and sessions differ by about 40%."
            />
          </Grid>
          <Grid item xs={6} sm={4} md={2}>
            <Figure
              label="CAGR"
              value={performance.cagr === null ? <Missing /> : percent(performance.cagr)}
              tone={performance.cagr > 0 ? 'up' : performance.cagr < 0 ? 'down' : undefined}
            />
          </Grid>
          <Grid item xs={6} sm={4} md={2}>
            <Figure
              label="Max drawdown"
              value={
                performance.maxDrawdown === null ? (
                  <Missing />
                ) : (
                  percent(performance.maxDrawdown)
                )
              }
              tone="down"
            />
          </Grid>
        </Grid>

        {performance.curveNote ? (
          <Alert severity="warning" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
            {performance.curveNote}
          </Alert>
        ) : null}

        {performance.concentrationNote ? (
          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" color="text.secondary">
              {performance.concentrationNote}
            </Typography>
            {performance.concentrationShare !== null &&
            performance.concentrationShare !== undefined ? (
              <LinearProgress
                variant="determinate"
                value={Math.min(performance.concentrationShare * 100, 100)}
                sx={{ mt: 1, height: 6, borderRadius: 3 }}
              />
            ) : null}
          </Box>
        ) : null}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2.5 }}>
        <Typography variant="overline" color="text.secondary">
          Exit mix
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          Counted from the stop records, not from reading a reason string. The
          backtest&apos;s mix is 348 trailing stops against 324 rotations — and
          the trail exits are only 34% profitable while still averaging +2.45%,
          because winners leave through the ratchet too. That is why the stop
          must not be tightened.
        </Typography>
        {mix.total ? (
          <Stack direction="row" spacing={1} flexWrap="wrap">
            {(mix.byKind ?? []).map((entry) => (
              <Chip
                key={entry.kind}
                size="small"
                label={`${entry.label}: ${entry.count} (${percent(entry.share, 0)})`}
                sx={{ bgcolor: 'action.selected', color: 'text.primary' }}
              />
            ))}
          </Stack>
        ) : (
          <Typography variant="body2" color="text.disabled">
            No position has left the book yet.
          </Typography>
        )}
      </Paper>
    </Stack>
  );
}

export default function SwingMomentumPage() {
  const theme = useTheme();
  const { isAdmin } = useAuth();
  const { activeId: portfolioId } = useActivePortfolio();

  const [status, setStatus] = useState(null);
  const [book, setBook] = useState(null);
  const [history, setHistory] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [openSession, setOpenSession] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [explain, setExplain] = useState(null);
  const [busy, setBusy] = useState(null);
  const [loading, setLoading] = useState(true);

  // The tab lives in the URL so "read this page" is a link somebody can send.
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'how-it-works' ? 'how-it-works' : 'live';

  const load = useCallback(async () => {
    try {
      const [nextStatus, nextHistory] = await Promise.all([
        swingApi.status(STRATEGY, portfolioId),
        swingApi.history(STRATEGY, 30),
      ]);
      setStatus(nextStatus);
      setHistory(nextHistory);
      if (portfolioId) {
        const [nextBook, nextPerformance] = await Promise.all([
          swingApi.book(STRATEGY, portfolioId),
          swingApi.performance(STRATEGY, portfolioId),
        ]);
        setBook(nextBook);
        setPerformance(nextPerformance);
      }
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [portfolioId]);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  // The rule as configured. Fetched once: it changes when someone edits the
  // strategy YAML and restarts, not every ten seconds.
  useEffect(() => {
    let cancelled = false;
    swingApi
      .explain(STRATEGY)
      .then((payload) => {
        if (!cancelled) setExplain(payload);
      })
      .catch(() => {
        // The live tab must still work if this fails; the explainer says so.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const openDetail = async (sessionId) => {
    if (openSession === sessionId) {
      setOpenSession(null);
      return;
    }
    setOpenSession(sessionId);
    setDetail(null);
    try {
      setDetail(await swingApi.session(sessionId, STRATEGY));
    } catch (detailError) {
      setError(detailError.message);
    }
  };

  const run = async (kind) => {
    if (!portfolioId) return;
    setBusy(kind);
    setNotice(null);
    try {
      const result =
        kind === 'nightly'
          ? await swingApi.runNightly(STRATEGY, portfolioId)
          : await swingApi.runRebalance(STRATEGY, portfolioId);
      setNotice(result.message ?? 'Done.');
      await load();
    } catch (runError) {
      setError(runError.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h2">{status?.label ?? 'NSE Swing Momentum'}</Typography>
        <Typography variant="body2" color="text.secondary">
          {status?.description ??
            'A breadth-gated momentum rotation on the Nifty 500.'}
        </Typography>
      </Box>

      <Alert severity="info" icon={<InfoOutlinedIcon />}>
        {status?.trackRecord?.note ??
          'No live track record. Every figure here comes from paper orders in this database.'}
      </Alert>

      <Tabs
        value={tab}
        onChange={(event, next) =>
          setParams(next === 'live' ? {} : { tab: next }, { replace: true })
        }
        sx={{ borderBottom: 1, borderColor: 'divider' }}
      >
        <Tab value="live" label="Live" />
        <Tab value="how-it-works" label="How it works" />
      </Tabs>

      {tab === 'how-it-works' ? (
        <SwingExplainer explain={explain} status={status} />
      ) : (
        <>

      {error ? <Alert severity="error" onClose={() => setError(null)}>{error}</Alert> : null}
      {notice ? (
        <Alert severity="success" onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      ) : null}
      {loading && !status ? <CircularProgress size={22} /> : null}

      <ActivityStrip status={status} />
      <ArmingCard status={status} />
      <GateCard status={status} />

      {isAdmin && portfolioId ? (
        <Stack direction="row" spacing={1} flexWrap="wrap">
          <Button
            variant="outlined"
            disabled={busy !== null || !status?.enabled}
            onClick={() => run('nightly')}
          >
            {busy === 'nightly' ? 'Deciding…' : 'Run nightly now'}
          </Button>
          <Button
            variant="outlined"
            color="warning"
            disabled={busy !== null || !status?.enabled}
            onClick={() => run('rebalance')}
          >
            {busy === 'rebalance' ? 'Rebalancing…' : 'Run rebalance now'}
          </Button>
          <Typography variant="caption" color="text.disabled" sx={{ alignSelf: 'center' }}>
            The nightly run never places an order. The rebalance places one only
            when the strategy is armed.
          </Typography>
        </Stack>
      ) : null}

      <Box>
        <Typography variant="overline" color="text.secondary">
          Open book
        </Typography>
        <Box sx={{ mt: 1 }}>
          {!portfolioId ? (
            <Alert severity="info">Pick a portfolio to see its book.</Alert>
          ) : (
            <BookTable book={book} />
          )}
        </Box>
        {book?.unmarkedPositions ? (
          <Typography variant="caption" color="text.disabled" sx={{ mt: 1, display: 'block' }}>
            {book.unmarkedPositions} position(s) have no live mark, so their
            unrealised P&amp;L and the portfolio&apos;s equity are withheld
            rather than shown as zero.
          </Typography>
        ) : null}
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary">
          Ranking — top {status?.snapshot?.ranking?.length ?? 15}
        </Typography>
        <Box sx={{ mt: 1 }}>
          <RankingTable snapshot={status?.snapshot} />
        </Box>
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary">
          Performance
        </Typography>
        <Box sx={{ mt: 1 }}>
          {portfolioId ? (
            <PerformanceCard performance={performance} />
          ) : (
            <Alert severity="info">Pick a portfolio to see its performance.</Alert>
          )}
        </Box>
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary">
          Recent scheduled runs
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          What the clock has done since this process started. It lives in
          memory, not in the database — the record is the decision history
          below.
        </Typography>
        <Box sx={{ mt: 1 }}>
          <ActivityLog status={status} />
        </Box>
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary">
          Decision history
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          Append-only. A record is never edited; a run that reconsiders writes a
          new one. Click a row for every decision it took, including the ones
          that produced no trade.
        </Typography>
        <TableContainer component={Paper} variant="outlined" sx={{ mt: 1 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Session</TableCell>
                <TableCell>Run</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Gate</TableCell>
                <TableCell align="right">Breadth</TableCell>
                <TableCell align="right">Slots</TableCell>
                <TableCell>Message</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {(history?.sessions ?? []).map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  open={openSession === session.id}
                  detail={openSession === session.id ? detail : null}
                  onOpen={openDetail}
                />
              ))}
              {!history?.sessions?.length ? (
                <TableRow>
                  <TableCell colSpan={8}>
                    <Typography variant="body2" color="text.disabled">
                      Nothing recorded yet. The nightly run writes a record every
                      session, including the sessions where it decides to do
                      nothing.
                    </Typography>
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>
        </>
      )}
    </Stack>
  );
}
