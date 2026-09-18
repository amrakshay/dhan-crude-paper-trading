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
import { formatPrice, formatQty } from '../utils/format';

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

function percent(value, digits = 1) {
  if (value === null || value === undefined) return null;
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function GateCard({ status }) {
  const snapshot = status?.snapshot;
  const regime = snapshot?.regime;
  const gate = snapshot?.effectiveGate;

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
        <Typography variant="h5" sx={{ fontWeight: 600 }}>
          {gateOn ? 'Regime gate ON' : 'Regime gate OFF'}
        </Typography>
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
            hint="The 63-session index return has to be above zero for a NEW entry. It never forces an exit."
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

function ArmingCard({ status }) {
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
