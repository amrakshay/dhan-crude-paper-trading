import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  LinearProgress,
  Link as MuiLink,
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
import { btstApi } from '../api/btst';
import { connectionsApi } from '../api/connections';
import AlertsPanel from '../components/AlertsPanel';
import JobProgress from '../components/JobProgress';
import BtstConfiguration from '../components/BtstConfiguration';
import BtstFunnel from '../components/BtstFunnel';
import BtstExplainer from '../components/BtstExplainer';
import BtstHealth from '../components/BtstHealth';
import BtstSignals from '../components/BtstSignals';
import { useAuth } from '../auth/AuthContext';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { formatCountdownLong, formatPrice, formatQty } from '../utils/format';

const STRATEGY = 'nse-btst-overnight';

/**
 * NSE BTST Overnight.
 *
 * The second strategy in this application that trades with nobody watching,
 * and the first whose EXIT is the edge. `frontend/CLAUDE.md` section 3's
 * honesty rules apply here the way they do on the rotation's page, plus two
 * that are specific to this strategy:
 *
 * - **"Nothing qualified today" is the NORMAL state.** At roughly half a
 *   signal a session, most days produce nothing at all. A page that looked
 *   broken when nothing qualified would be wrong about this strategy more
 *   often than it was right, so the filter funnel is always on screen: "289
 *   tradable, 284 measured, 31 above their 55-day high, 6 on 2x volume, 0
 *   closing strong" reads as a working scan and a bare "no candidates" does
 *   not.
 * - **A late exit is shown as late, in the colour of a problem.** Section 10.1
 *   of the specification: the same positions held to the next close instead of
 *   the next open measure a 49.0% win rate against 71.4%. Holding past the
 *   open is not a delay, it is a different strategy.
 *
 * FIVE TABS, where the rotation has four. The extra one is Signals, and it is
 * the thing the rotation does not need: its decision happens overnight in one
 * shot, while this one forms over the afternoon and is the most interesting
 * thing on the screen between 14:30 and 15:20.
 *
 * The page polls at 10 s and opens no socket. Nothing on it moves at tick
 * speed, and a page that is a RECORD must not depend on the feed being healthy
 * to show what was decided.
 */

const POLL_MS = 10000;

/** The stored run kind in the words the page uses. The DATABASE keeps SCAN and
 *  EXIT; renaming stored values would rewrite history, so the translation
 *  belongs here, at the edge. */
const RUN_LABELS = {
  SCAN: 'Afternoon scan',
  EXIT: 'Morning exit',
  MANUAL: 'Manual run',
};

const EXIT_LABELS = {
  PENDING: 'Held overnight',
  EXITED: 'Sold at the open',
  EXITED_LATE: 'SOLD LATE',
  FAILED: 'STILL HELD',
  // The position was gone before the exit reached it -- closed by hand, or by
  // an earlier pass. Nothing was sold, and nothing is held. Deliberately not
  // grouped with the two alarm states below: there is nothing to act on.
  NOT_HELD: 'Closed elsewhere',
};

const TABS = [
  { key: 'live', label: 'Live' },
  { key: 'signals', label: 'Signals' },
  { key: 'configuration', label: 'Configuration' },
  { key: 'health', label: 'Health', adminOnly: true },
  { key: 'alerts', label: 'Alerts', adminOnly: true },
  { key: 'how-it-works', label: 'How it works' },
];

export default function BtstOvernightPage() {
  const theme = useTheme();
  const { isAdmin } = useAuth();
  // `activeId`, which is what the context actually provides. It was read as
  // `activePortfolioId` until 2026-09-19 — a name nothing exports — so every
  // request from this page went out with no portfolio at all: no balance, the
  // whole strategy's holdings rather than this book's, and the two run buttons
  // posting an undefined id. This page is the only place that name appeared.
  const { activeId: portfolioId } = useActivePortfolio();
  const [searchParams, setSearchParams] = useSearchParams();

  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  const [catalogue, setCatalogue] = useState(null);
  const [catalogueError, setCatalogueError] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);

  // The tab is in the URL so "read this page" is a link somebody can send.
  // `?tab=health` falls back to Live for a ROLE_USER, which is presentation:
  // the endpoint refuses them with a 403 regardless.
  const visibleTabs = useMemo(
    () => TABS.filter((tab) => !tab.adminOnly || isAdmin),
    [isAdmin],
  );
  const requested = searchParams.get('tab') || 'live';
  const tab = visibleTabs.some((one) => one.key === requested) ? requested : 'live';

  const load = useCallback(async () => {
    try {
      const [nextStatus, nextHistory, nextPerformance] = await Promise.all([
        btstApi.status(STRATEGY, portfolioId),
        btstApi.history(STRATEGY, 30),
        btstApi.performance(STRATEGY),
      ]);
      setStatus(nextStatus);
      setHistory(nextHistory);
      setPerformance(nextPerformance);
      setError(null);
    } catch (caught) {
      setError(caught.message || 'Could not read the strategy');
    } finally {
      setLoading(false);
    }

    // On the SAME cadence, not a second loop: a page that reports on load must
    // not be a load source. Both are admin-only and BOTH failures are kept off
    // `error`, so a 403 or a hiccup on an admin read cannot blank the Live tab
    // beside it. This is the rotation's arrangement (frontend/CLAUDE.md §5d);
    // the Health tab used to poll on its own, which meant two clocks asking
    // the same question at the same rate.
    if (!isAdmin) return;
    try {
      setHealth(await btstApi.health(STRATEGY, portfolioId));
      setHealthError(null);
    } catch (problem) {
      setHealthError(problem.message);
    }

    // The alert catalogue, filtered to THIS strategy. `btst-exit-incomplete`
    // is `strategy_scoped`, so it appears here and NOT on the system health
    // page — the server filters on `alerts.strategy_key` and two pages showing
    // one rule would disagree the moment either changed (§4a).
    try {
      setCatalogue(await connectionsApi.catalogue(STRATEGY));
      setCatalogueError(null);
    } catch (problem) {
      setCatalogueError(problem.message);
    }
  }, [portfolioId, isAdmin]);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const runScan = async (placeOrders) => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await btstApi.runScan(STRATEGY, portfolioId, {
        placeOrders,
      });
      const run = (result.runs || [])[0];
      setNotice(run?.message || 'The scan ran.');
      await load();
    } catch (caught) {
      setNotice(caught.message || 'The scan could not run');
    } finally {
      setBusy(false);
    }
  };

  const runExit = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await btstApi.runExit(STRATEGY, portfolioId);
      const run = (result.runs || [])[0];
      setNotice(run?.message || 'The exit ran.');
      await load();
    } catch (caught) {
      setNotice(caught.message || 'The exit could not run');
    } finally {
      setBusy(false);
    }
  };

  if (loading && !status) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        alignItems={{ sm: 'center' }}
        justifyContent="space-between"
        spacing={1}
        sx={{ mb: 2 }}
      >
        <Box>
          <Typography variant="h5">{status?.label || 'BTST Overnight'}</Typography>
          {/* The strategy's own one-liner, from its YAML by way of the
              payload. The fallback is only for the first paint. */}
          <Typography variant="body2" color="text.secondary">
            {status?.description ||
              'Buy today, sell tomorrow. The edge is the overnight gap and nothing else.'}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <StateChip
            on={status?.enabled}
            onLabel="ENABLED"
            offLabel="OFF"
            help="Whether it scans and journals at all."
          />
          <StateChip
            on={status?.armed}
            onLabel="ARMED"
            offLabel="NOT ARMED"
            help="Whether it may place an order. Two switches, because this is software that spends money unattended."
          />
        </Stack>
      </Stack>

      {/* Above the numbers, always, and composed by the SERVER rather than
          here: the specification's figures are an in-sample, survivorship-
          biased backtest of a rule that has never traded a rupee. */}
      {status?.noTrackRecord ? (
        <Alert severity="info" icon={<InfoOutlinedIcon />} sx={{ mb: 2 }}>
          {status.noTrackRecord}
        </Alert>
      ) : null}

      {error ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      ) : null}
      {notice ? (
        <Alert severity="info" sx={{ mb: 2 }} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      ) : null}

      <Tabs
        value={tab}
        onChange={(_event, value) => setSearchParams({ tab: value })}
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
      >
        {visibleTabs.map((one) => (
          <Tab key={one.key} value={one.key} label={one.label} />
        ))}
      </Tabs>

      {tab === 'live' ? (
        <LiveTab
          status={status}
          history={history}
          performance={performance}
          theme={theme}
          isAdmin={isAdmin}
          busy={busy}
          onScan={runScan}
          onExit={runExit}
        />
      ) : null}
      {/* The one read that does NOT ride the page's poll, and deliberately.
          `/btst/signals` runs the whole filter funnel over the universe on
          every call; putting it on the page's cadence would run it every ten
          seconds for somebody reading the journal, which is precisely the load
          the one-poll rule exists to prevent. It is mounted with the tab, so
          it runs while somebody is looking at it and not otherwise. */}
      {tab === 'signals' ? (
        <BtstSignals strategyKey={STRATEGY} portfolioId={portfolioId} />
      ) : null}
      {tab === 'configuration' ? (
        <BtstConfiguration strategyKey={STRATEGY} isAdmin={isAdmin} />
      ) : null}
      {tab === 'health' && isAdmin ? (
        <BtstHealth health={health} error={healthError} />
      ) : null}
      {/* The rules THIS strategy will tell somebody about — including
          `btst-exit-incomplete`, which guards the thing §10.1 says IS the
          strategy. Process-wide rules stay on the system health page. */}
      {tab === 'alerts' && isAdmin ? (
        <AlertsPanel
          catalogue={catalogue}
          error={catalogueError}
          loading={loading}
          strategyKey={STRATEGY}
        />
      ) : null}
      {tab === 'how-it-works' ? (
        <BtstExplainer strategyKey={STRATEGY} status={status} />
      ) : null}
    </Box>
  );
}

/** ENABLED and ARMED as two chips, never one. */
function StateChip({ on, onLabel, offLabel, help }) {
  const theme = useTheme();
  return (
    <Tooltip title={help}>
      <Chip
        size="small"
        label={on ? onLabel : offLabel}
        sx={{
          bgcolor: on ? theme.market.upSoft : theme.palette.action.hover,
          color: on ? theme.market.up : theme.palette.text.secondary,
          fontWeight: 600,
        }}
      />
    </Tooltip>
  );
}

function LiveTab({ status, history, performance, theme, isAdmin, busy, onScan, onExit }) {
  const holdings = status?.holdings || [];
  const overdue = holdings.filter(
    (one) => one.exitStatus === 'FAILED' || one.exitStatus === 'EXITED_LATE',
  );

  return (
    <Stack spacing={2}>
      {overdue.length ? (
        <Alert severity="error" icon={<WarningAmberIcon />}>
          {overdue.length} position(s) did not leave at the open. The overnight
          gap is this strategy's entire edge and it is given back during the
          session — the same positions held to the next close measure a 49.0%
          win rate against 71.4%.
        </Alert>
      ) : null}

      {/* WHAT IT IS DOING NOW, above what it decided. */}
      <ActivityStrip status={status} theme={theme} />

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Grid container spacing={2}>
          <Grid item xs={12} sm={6} md={3}>
            <Metric
              label="Market"
              value={status?.market?.open ? 'Open' : 'Closed'}
              hint={`${status?.market?.opensAtIst}–${status?.market?.closesAtIst} IST`}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Metric
              label="Next scan"
              value={<Countdown iso={status?.schedule?.nextScanIst} />}
              hint={`${status?.schedule?.scanAtIst} IST — decides and buys`}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Metric
              label="Next exit"
              value={<Countdown iso={status?.schedule?.nextExitIst} />}
              hint={`${status?.schedule?.exitAtIst} IST — sells everything`}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Metric
              label="Held overnight"
              value={holdings.length}
              hint={
                holdings.length
                  ? 'Due out at the next open, unconditionally'
                  : 'Nothing held — the ordinary state'
              }
            />
          </Grid>
        </Grid>

        {status?.signalFrequencyNote ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
            {status.signalFrequencyNote}
          </Typography>
        ) : null}

        {isAdmin ? (
          <Stack direction="row" spacing={1} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
            <Button
              size="small"
              variant="outlined"
              disabled={busy}
              onClick={() => onScan(false)}
            >
              Scan without trading
            </Button>
            <Button size="small" variant="outlined" disabled={busy} onClick={() => onScan(true)}>
              Run the scan now
            </Button>
            <Button size="small" variant="outlined" disabled={busy} onClick={onExit}>
              Exit everything now
            </Button>
            {busy ? <CircularProgress size={20} sx={{ alignSelf: 'center' }} /> : null}
          </Stack>
        ) : null}
      </Paper>

      {/* Four figures, never one (§3). This strategy deploys about a third of
          the book in a single afternoon pass, so the money belongs on the tab
          that says what it is about to do. */}
      <MoneyCard balance={status?.balance} />

      <HoldingsCard holdings={holdings} theme={theme} />
      <PerformanceCard performance={performance} />

      {/* ONE LINE about the exit timing, linking to the table rather than
          repeating it. §9.4's decay table is on "How it works" in full. */}
      {status?.exitTimingNote ? (
        <Alert severity="info" icon={<InfoOutlinedIcon />}>
          {status.exitTimingNote}{' '}
          <MuiLink component={RouterLink} to="/btst?tab=how-it-works">
            The year-by-year decay is on “How it works”.
          </MuiLink>
        </Alert>
      ) : null}

      <HistoryCard history={history} />
    </Stack>
  );
}

/**
 * WHAT IT IS DOING THIS SECOND — not what it decided.
 *
 * The rotation's equivalent reads its row out of the scheduler's own
 * `schedules` list; this strategy is not in that list at all (the rows are
 * built from `SwingParameters`, which this module's YAML cannot satisfy), so
 * the server composes a BTST-shaped view instead and this renders it.
 *
 * Every countdown ticks LOCALLY off an absolute timestamp, so a stalled poll
 * shows up as a clock running past a run that never happened rather than as a
 * number frozen at something plausible.
 */
function ActivityStrip({ status, theme }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (!status) {
    return (
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <CircularProgress size={16} />
          <Typography variant="body2" color="text.secondary">
            Reading what the strategy is doing…
          </Typography>
        </Stack>
      </Paper>
    );
  }

  const scheduler = status.scheduler || {};
  const window = status.subscriptionWindow;
  const last = (scheduler.recent || [])[0];
  const missed = scheduler.missedRunCount ?? 0;
  const notEnforced = (status.policies || []).filter((one) => !one.enforced);

  // Three states, not two: a scheduler that is not running, one that is idle,
  // and one that is mid-job each read differently.
  const activity =
    scheduler.running === false
      ? 'the clock is not running'
      : scheduler.activity || 'idle — waiting for the next scheduled run';

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
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Chip
            size="small"
            label={status.market?.open ? 'market OPEN' : 'market closed'}
            sx={{
              bgcolor: status.market?.open ? theme.market.upSoft : 'action.selected',
              color: status.market?.open ? theme.market.up : 'text.secondary',
              fontWeight: 600,
            }}
          />
          <Typography variant="body2" className="numeric" color="text.secondary">
            {scheduler.nowIst
              ? `${new Date(scheduler.nowIst).toLocaleTimeString()} IST`
              : 'clock unavailable'}
          </Typography>
          {window ? (
            <Tooltip title={window.note || ''}>
              <Chip
                size="small"
                variant="outlined"
                label={
                  window.open
                    ? `universe on the feed (${window.universeSize ?? '?'})`
                    : `universe joins at ${window.opensAtIst}`
                }
              />
            </Tooltip>
          ) : null}
        </Stack>

        <Stack direction="row" spacing={1} alignItems="center">
          {scheduler.activity && !scheduler.progress ? (
            <CircularProgress size={14} />
          ) : null}
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {activity}
          </Typography>
        </Stack>
      </Stack>

      {/* A long job, where somebody watching the strategy is already looking.
          It is NOT this strategy's own work — nothing here has a long job —
          so the server sends the sentence saying whose it is, and the bar is
          rendered under it rather than unattributed. */}
      {scheduler.progress ? (
        <Box sx={{ mt: 1 }}>
          <JobProgress progress={scheduler.progress} compact />
          {scheduler.progressNote ? (
            <Typography variant="caption" color="text.secondary">
              {scheduler.progressNote}
            </Typography>
          ) : null}
        </Box>
      ) : null}

      {!status.enabled ? (
        <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mt: 2 }}>
          Switched OFF. Nothing is scanned, nothing is journalled and the
          universe does not join the feed. The record below is unchanged, and an
          open position can still be closed.
        </Alert>
      ) : !status.armed ? (
        <Alert severity="info" icon={<InfoOutlinedIcon />} sx={{ mt: 2 }}>
          AUTO TRADE IS OFF. It will scan, decide and write the identical
          decision record at 15:20, and it will place no order at all — which is
          what makes arming a safeguard rather than a mode.
        </Alert>
      ) : null}

      {/* A gate that looks ON must never be shown while nothing obeys it. */}
      {notEnforced.length ? (
        <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mt: 2 }}>
          {notEnforced.length} rule
          {notEnforced.length === 1 ? ' is' : 's are'} NOT ENFORCED:{' '}
          {notEnforced.map((one) => one.label).join(', ')}. It is still computed
          and recorded on every decision, which is what makes its cost
          measurable afterwards.{' '}
          <MuiLink component={RouterLink} to="/btst?tab=configuration">
            Change it on the Configuration tab.
          </MuiLink>
        </Alert>
      ) : null}

      <Divider sx={{ my: 2 }} />

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} md={3}>
          <Metric
            label="Next scan"
            value={<Countdown iso={status.schedule?.nextScanIst} prefix="in " />}
            hint={`${status.schedule?.scanAtIst} IST — reads the session so far, decides and buys, in one pass`}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Metric
            label="Next exit"
            value={<Countdown iso={status.schedule?.nextExitIst} prefix="in " />}
            hint={`${status.schedule?.exitAtIst} IST — sells everything held, unconditionally`}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Metric
            label="What it last did"
            value={
              last ? `${(RUN_LABELS[last.kind] || last.kind).toLowerCase()} ${last.ok ? 'ok' : 'FAILED'}` : 'none this process'
            }
            hint={
              last
                ? `${new Date(last.atIst).toLocaleString()} — ${last.detail || 'no detail'}`
                : scheduler.runListNote
            }
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Metric
            label="Missed runs"
            value={missed}
            hint="Sessions with no record. Reported, never silently re-decided later on bars that may since have been restated."
          />
        </Grid>
      </Grid>

      {missed > 0 ? (
        <Alert severity="warning" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          {missed} session{missed === 1 ? '' : 's'} with no record. A missed
          SCAN costs that session's signals; a missed EXIT is the one that
          costs the thesis.{' '}
          <MuiLink component={RouterLink} to="/btst?tab=health">
            The dates are on the Health tab.
          </MuiLink>
        </Alert>
      ) : null}

      {scheduler.error ? (
        <Typography variant="caption" color="error.main" sx={{ mt: 1, display: 'block' }}>
          clock error: {scheduler.error}
        </Typography>
      ) : null}

      {/* The closing auction, which this strategy needs stated more than the
          rotation does: its scan is INSIDE the window. Composed by the server
          from the two configured times rather than asserted here. */}
      {status.closingAuction?.note ? (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
          {status.closingAuction.note}
        </Typography>
      ) : null}

      {scheduler.clockNote ? (
        <Typography variant="caption" color="text.disabled" sx={{ mt: 1, display: 'block' }}>
          {scheduler.clockNote}
        </Typography>
      ) : null}
    </Paper>
  );
}

/**
 * The portfolio's four figures.
 *
 * Never one: collapsing them is what makes a position's effect invisible, and
 * blocked margin always says "estimate" because it is a configured
 * approximation rather than what a broker would hold. An equity figure
 * `BalanceService` withheld says so and names who is responsible, rather than
 * valuing an unmarked position at zero.
 */
function MoneyCard({ balance }) {
  if (!balance) {
    return (
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
          The book
        </Typography>
        <Typography variant="body2" color="text.secondary">
          No portfolio is in scope, so there is no money to show. Pick one in
          the header — this strategy sizes every entry against total equity.
        </Typography>
      </Paper>
    );
  }

  const withheld = balance.equity === null || balance.equity === undefined;
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ mb: 1 }}>
        The book
      </Typography>
      <Grid container spacing={2}>
        <Grid item xs={6} md={3}>
          <Metric label="Cash" value={formatPrice(balance.cash)} />
        </Grid>
        <Grid item xs={6} md={3}>
          <Metric
            label="Blocked margin"
            value={formatPrice(balance.blockedMargin)}
            hint="An estimate from a configured model, never what a broker would hold."
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <Metric
            label="Available"
            value={formatPrice(balance.available)}
            hint="What an entry is checked against — at placement AND again at the fill."
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <Metric
            label="Equity"
            value={
              withheld ? (
                <Typography variant="body2" color="text.disabled">
                  no mark
                </Typography>
              ) : (
                formatPrice(balance.equity)
              )
            }
            hint="B12 sizes each entry as total equity divided by the slot count."
          />
        </Grid>
      </Grid>
      <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
        Blocked margin is an estimate.
        {balance.unmarkedPositions
          ? ` ${balance.unmarkedPositions} open position(s) have no live mark${
              (balance.unmarkedStrategies || []).length
                ? ` (${balance.unmarkedStrategies.join(', ')})`
                : ''
            }, so equity is withheld rather than shown with them valued at zero.`
          : ''}
      </Typography>
      {withheld ? (
        <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mt: 1.5 }}>
          Equity cannot be computed, so the next scan would size nothing and say
          why. B12 divides total equity by the slot count.
        </Alert>
      ) : null}
    </Paper>
  );
}

function Metric({ label, value, hint }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h6" className="numeric">
        {value ?? '—'}
      </Typography>
      {hint ? (
        <Typography variant="caption" color="text.secondary">
          {hint}
        </Typography>
      ) : null}
    </Box>
  );
}

/** Ticks LOCALLY off the absolute timestamp the server sent, rather than
 *  re-fetching every second. A countdown that cannot be computed says so. */
function Countdown({ iso, prefix = '' }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  if (!iso) return 'not scheduled';
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  if (Number.isNaN(seconds)) return 'unknown';
  return `${prefix}${formatCountdownLong(seconds)}`;
}

function HoldingsCard({ holdings, theme }) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ mb: 1 }}>
        Held overnight
      </Typography>
      {/* No separate ranking table, deliberately. The rotation has one because
          it holds ten names for weeks and the rank is what decides which; this
          holds up to five for eighteen hours, the Signals tab IS the ranking,
          and what is worth knowing about a position already open is the
          measurement that qualified it — which is on this row. */}
      {holdings.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Nothing is held. At about half a signal a session this is the ordinary
          state, not an idle strategy.
        </Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell align="right">Qty</TableCell>
                <TableCell align="right">Entry</TableCell>
                <TableCell>Entered</TableCell>
                <TableCell>Why it was bought</TableCell>
                <TableCell>State</TableCell>
                <TableCell align="right">Overnight gap</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {holdings.map((one) => (
                <TableRow key={one.id}>
                  <TableCell>{one.symbol}</TableCell>
                  <TableCell align="right" className="numeric">
                    {formatQty(one.quantity)}
                  </TableCell>
                  <TableCell align="right" className="numeric">
                    {formatPrice(one.entryPrice)}
                  </TableCell>
                  <TableCell>{one.entrySessionDate}</TableCell>
                  <TableCell sx={{ maxWidth: 320 }}>
                    {/* Null is not "no reason": a position entered by hand, or
                        one whose order id was never recorded, has no decision
                        row to explain it, and that reads differently. */}
                    {one.entry ? (
                      <Stack spacing={0.25}>
                        <Typography variant="caption">
                          {one.entry.reason}
                        </Typography>
                        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                          {one.entry.rank !== null && one.entry.rank !== undefined ? (
                            <Chip size="small" variant="outlined" label={`rank ${one.entry.rank}`} />
                          ) : null}
                          {one.entry.volRatio !== null && one.entry.volRatio !== undefined ? (
                            <Chip
                              size="small"
                              variant="outlined"
                              label={`${Number(one.entry.volRatio).toFixed(1)}× volume`}
                            />
                          ) : null}
                          {one.entry.clv !== null && one.entry.clv !== undefined ? (
                            <Chip
                              size="small"
                              variant="outlined"
                              label={`CLV ${Number(one.entry.clv).toFixed(2)}`}
                            />
                          ) : null}
                        </Stack>
                      </Stack>
                    ) : (
                      <Typography variant="caption" color="text.disabled">
                        no decision record for this entry
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={EXIT_LABELS[one.exitStatus] || one.exitStatus}
                      sx={{
                        bgcolor:
                          one.exitStatus === 'FAILED' || one.exitStatus === 'EXITED_LATE'
                            ? theme.market.downSoft
                            : theme.palette.action.hover,
                        color:
                          one.exitStatus === 'FAILED' || one.exitStatus === 'EXITED_LATE'
                            ? theme.market.down
                            : theme.palette.text.secondary,
                      }}
                    />
                    {one.exitDelayMinutes ? (
                      <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                        {one.exitDelayMinutes} min late
                      </Typography>
                    ) : null}
                  </TableCell>
                  <TableCell align="right" className="numeric">
                    {/* Null until it is sold. A position still held has no
                        realised gap, and 0.00% would be a measurement of
                        something that has not happened. */}
                    {one.overnightGap === null || one.overnightGap === undefined ? (
                      <Typography variant="caption" color="text.secondary">
                        not sold yet
                      </Typography>
                    ) : (
                      <GapValue value={one.overnightGap} theme={theme} />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
}

function GapValue({ value, theme }) {
  const number = Number(value);
  if (Number.isNaN(number)) return <span>—</span>;
  return (
    <span style={{ color: number >= 0 ? theme.market.up : theme.market.down }}>
      {`${number >= 0 ? '+' : ''}${(number * 100).toFixed(2)}%`}
    </span>
  );
}

function PerformanceCard({ performance }) {
  const theme = useTheme();
  if (!performance) return null;
  const benchmark = performance.benchmark || {};
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ mb: 1 }}>
        Realised overnight gaps
      </Typography>
      <Grid container spacing={2}>
        <Grid item xs={6} md={3}>
          <Metric label="Round trips" value={performance.trades} />
        </Grid>
        <Grid item xs={6} md={3}>
          {/* Undefined, not zero: no closed round trip means no measured edge,
              which is a different answer from an edge of nothing. */}
          <Metric
            label="Mean gap"
            value={
              performance.meanGap === null ? (
                <Typography variant="body2" color="text.secondary">
                  not measured
                </Typography>
              ) : (
                <GapValue value={performance.meanGap} theme={theme} />
              )
            }
            hint={`backtest: +${(benchmark.grossMeanGap * 100).toFixed(3)}% gross`}
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <Metric
            label="Win rate"
            value={
              performance.winRate === null
                ? 'not measured'
                : `${(performance.winRate * 100).toFixed(1)}%`
            }
            hint={`backtest: ${(benchmark.grossWinRate * 100).toFixed(1)}%`}
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <Metric
            label="Late exits"
            value={performance.lateExits ?? 0}
            hint="each one gave back part of the edge"
          />
        </Grid>
      </Grid>
      {benchmark.note ? (
        <Alert severity="warning" icon={<InfoOutlinedIcon />} sx={{ mt: 2 }}>
          {benchmark.note}
        </Alert>
      ) : null}
    </Paper>
  );
}

function HistoryCard({ history }) {
  const sessions = history?.sessions || [];
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ mb: 1 }}>
        Decision history
      </Typography>
      {sessions.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Nothing has run yet. The scan runs once a session and writes a record
          whether or not anything qualified.
        </Typography>
      ) : (
        <Stack spacing={1.5}>
          {sessions.map((session) => (
            <Box key={session.id}>
              <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap">
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {session.sessionDate}
                </Typography>
                <Chip size="small" label={RUN_LABELS[session.runKind] || session.runKind} />
                <Chip size="small" variant="outlined" label={session.status} />
                {session.regimeEnforced === false ? (
                  <Chip size="small" color="warning" label="GATE NOT ENFORCED" />
                ) : null}
                {session.fnoExcluded === false ? (
                  <Chip size="small" variant="outlined" label="F&O INCLUDED" />
                ) : null}
              </Stack>
              <Typography variant="body2" color="text.secondary">
                {session.message}
              </Typography>
              {session.funnel?.some((one) => one.count !== null) ? (
                <BtstFunnel funnel={session.funnel} />
              ) : null}
              {session.decisions?.length ? (
                <TableContainer sx={{ mt: 1 }}>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>Symbol</TableCell>
                        <TableCell align="right">Rank</TableCell>
                        <TableCell>Action</TableCell>
                        <TableCell align="right">Vol×</TableCell>
                        <TableCell align="right">CLV</TableCell>
                        <TableCell>Why</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {session.decisions.map((one, index) => (
                        <TableRow key={`${one.symbol}-${index}`}>
                          <TableCell>{one.symbol}</TableCell>
                          <TableCell align="right" className="numeric">
                            {one.rank ?? '—'}
                          </TableCell>
                          <TableCell>{one.action}</TableCell>
                          <TableCell align="right" className="numeric">
                            {one.volRatio === null || one.volRatio === undefined
                              ? '—'
                              : `${Number(one.volRatio).toFixed(1)}×`}
                          </TableCell>
                          <TableCell align="right" className="numeric">
                            {one.clv === null || one.clv === undefined
                              ? '—'
                              : Number(one.clv).toFixed(2)}
                          </TableCell>
                          <TableCell>
                            <Typography variant="caption">{one.reason}</Typography>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              ) : null}
              <Divider sx={{ mt: 1.5 }} />
            </Box>
          ))}
        </Stack>
      )}
    </Paper>
  );
}
