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
};

const TABS = [
  { key: 'live', label: 'Live' },
  { key: 'signals', label: 'Signals' },
  { key: 'configuration', label: 'Configuration' },
  { key: 'health', label: 'Health', adminOnly: true },
  { key: 'how-it-works', label: 'How it works' },
];

export default function BtstOvernightPage() {
  const theme = useTheme();
  const { isAdmin } = useAuth();
  const { activePortfolioId } = useActivePortfolio();
  const [searchParams, setSearchParams] = useSearchParams();

  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState(null);
  const [performance, setPerformance] = useState(null);
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
        btstApi.status(STRATEGY, activePortfolioId),
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
  }, [activePortfolioId]);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const runScan = async (placeOrders) => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await btstApi.runScan(STRATEGY, activePortfolioId, {
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
      const result = await btstApi.runExit(STRATEGY, activePortfolioId);
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
          <Typography variant="body2" color="text.secondary">
            Buy today, sell tomorrow. The edge is the overnight gap and nothing
            else.
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
      {tab === 'signals' ? (
        <BtstSignals strategyKey={STRATEGY} portfolioId={activePortfolioId} />
      ) : null}
      {tab === 'configuration' ? (
        <BtstConfiguration strategyKey={STRATEGY} isAdmin={isAdmin} />
      ) : null}
      {tab === 'health' && isAdmin ? (
        <BtstHealth strategyKey={STRATEGY} portfolioId={activePortfolioId} />
      ) : null}
      {tab === 'how-it-works' ? <BtstExplainer strategyKey={STRATEGY} /> : null}
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
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
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

      <HoldingsCard holdings={holdings} theme={theme} />
      <PerformanceCard performance={performance} />
      <HistoryCard history={history} />
    </Stack>
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
function Countdown({ iso }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  if (!iso) return 'not scheduled';
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  if (Number.isNaN(seconds)) return 'unknown';
  return formatCountdownLong(seconds);
}

function HoldingsCard({ holdings, theme }) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ mb: 1 }}>
        Held overnight
      </Typography>
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
