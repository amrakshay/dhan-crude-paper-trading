import { useEffect, useState } from 'react';
import {
  Alert,
  AlertTitle,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  Link as MuiLink,
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
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { Link as RouterLink } from 'react-router-dom';
import { formatCompact, formatQty, formatTime } from '../utils/format';

/**
 * The Swing Momentum page's Health tab.
 *
 * The system health page answers "what is this process doing", which is
 * process-wide and the right shape for a process. This answers the question an
 * operator actually has about a strategy that trades unattended:
 *
 *   Is it on, may it trade, are its jobs alive, are its prices fresh, what does
 *   it hold, what is it watching, which rules are in force and who changed them?
 *
 * Three rules it is written to:
 *
 * - **Plain words.** The screen says "auto trade", "analysis of stocks" and
 *   "order placement" while the code and the database still say armed, NIGHTLY
 *   and REBALANCE. The translation lives at the edge (`RUN_LABELS` on the
 *   page), because renaming stored values would rewrite history.
 * - **Undefined is not zero** (`frontend/CLAUDE.md` §3). A staleness that
 *   cannot be measured, a nearest stop with no marks, an equity figure
 *   `BalanceService` withheld and a countdown that cannot be computed each say
 *   so. The server sends null; this renders the reason.
 * - **It says what it does NOT know.** No audit history, an activity log that
 *   lives in memory, and stop-watcher counters kept once per process. All
 *   three are on screen rather than implied away.
 *
 * It adds no poll of its own: the page already polls at 10 s and fetches this
 * on the same cadence. A page that reports on load must not be a load source.
 *
 * It is ADMIN-ONLY. `/swing` itself is open to any signed-in user because a
 * journal is history; this tab is live machinery state and log records, and
 * `require_admin` on `GET /api/swing/health` is what enforces that -- hiding
 * the tab is presentation (`frontend/CLAUDE.md` §5).
 */

/** The stored run kind, in the words the rest of the page uses. */
const RUN_LABELS = { NIGHTLY: 'Analysis of stocks', REBALANCE: 'Order placement' };

function Missing({ children = 'not measured', hint }) {
  const body = (
    <Typography variant="body2" color="text.disabled" component="span">
      {children}
    </Typography>
  );
  return hint ? <Tooltip title={hint}>{body}</Tooltip> : body;
}

function Panel({ title, subtitle, children }) {
  return (
    <Card>
      <CardContent>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        {subtitle ? (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block', mt: 0.5, mb: 1.5 }}
          >
            {subtitle}
          </Typography>
        ) : (
          <Box sx={{ mb: 1.5 }} />
        )}
        {children}
      </CardContent>
    </Card>
  );
}

function Field({ label, value, hint, mono = false, color }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      justifyContent="space-between"
      alignItems="baseline"
    >
      <Tooltip title={hint || ''} placement="left">
        <Typography variant="body2" color="text.secondary" sx={{ flexShrink: 0 }}>
          {label}
        </Typography>
      </Tooltip>
      <Typography
        variant="body2"
        className={mono ? 'numeric' : undefined}
        sx={{ textAlign: 'right', wordBreak: 'break-word', color }}
      >
        {value ?? <Missing>—</Missing>}
      </Typography>
    </Stack>
  );
}

/**
 * One verdict: green, amber or red, with a sentence under it.
 *
 * `tone` is deliberately a third value rather than a boolean. "off" is not
 * "broken" -- a switched-off strategy is working exactly as configured -- and
 * colouring it red would make the panel cry wolf on the state an operator
 * chose.
 */
function Verdict({ label, state, detail, tone }) {
  const theme = useTheme();
  const colour =
    tone === 'good'
      ? theme.market.up
      : tone === 'bad'
        ? theme.market.down
        : tone === 'warn'
          ? theme.palette.warning.main
          : theme.palette.text.secondary;
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, color: colour }}>
        {state}
      </Typography>
      <Typography variant="caption" color="text.disabled">
        {detail}
      </Typography>
    </Stack>
  );
}

function jobTone(row) {
  if (row.state === 'running') return 'good';
  if (!row.expected) return 'neutral';
  return 'bad';
}

function jobState(row) {
  if (row.state === 'running') return 'running';
  if (row.state === 'not started') return 'not started';
  if (row.state === 'unexpected') return 'running, unexpectedly';
  return 'NOT RUNNING';
}

/** "Is it working right now?" */
function WorkingNow({ health }) {
  const working = health.working;
  const jobs = working.jobs.rows;
  const market = working.market;

  return (
    <Panel
      title="Is it working right now?"
      subtitle="Six facts, in the order they stop the strategy working. A strategy that is on and has auto trade on, with a dead clock, places nothing and says nothing — that is the failure this panel exists to make visible."
    >
      <Grid container spacing={2.5}>
        <Grid item xs={6} sm={4} md={2}>
          <Verdict
            label="Switched on"
            state={working.enabled ? 'ON' : 'OFF'}
            tone={working.enabled ? 'good' : 'neutral'}
            detail={
              working.enabled
                ? 'It computes, decides and records on every scheduled run.'
                : 'Nothing is scheduled and nothing is decided. Its history is unchanged.'
            }
          />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <Verdict
            label="Auto trade"
            state={working.armed ? 'ON' : 'OFF'}
            tone={working.armed ? 'warn' : 'neutral'}
            detail={
              working.armed
                ? 'It may place its own orders, on its own schedule, in paper money.'
                : 'It will decide and place nothing.'
            }
          />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <Verdict
            label="Market"
            state={market.open ? 'OPEN' : 'closed'}
            tone={market.open ? 'good' : 'neutral'}
            detail={
              health.nowIst
                ? `${new Date(health.nowIst).toLocaleTimeString()} IST · trades ${market.opensAtIst}–${market.closesAtIst}`
                : `trades ${market.opensAtIst}–${market.closesAtIst} IST`
            }
          />
        </Grid>
        {jobs.map((row) => (
          <Grid item xs={6} sm={4} md={2} key={row.name}>
            <Verdict
              label={row.role === 'the clock' ? 'Its clock' : 'Its stop watcher'}
              state={jobState(row)}
              tone={jobTone(row)}
              detail={
                row.expected
                  ? row.description
                  : 'Not expected to be running with the strategy in this state.'
              }
            />
          </Grid>
        ))}
        <Grid item xs={6} sm={4} md={2}>
          {/* Three states, not two. A subscription that was BUILT and holds
              nothing is not the same as one that was never built, and neither
              is healthy — a green "0 instruments" would be the page telling an
              operator that no prices are arriving in the colour it uses for
              everything being fine. */}
          <Verdict
            label="Prices arriving"
            state={
              !working.feed.subscribed
                ? 'not subscribed'
                : working.feed.instrumentCount
                  ? `${formatQty(working.feed.instrumentCount)} instruments`
                  : 'nothing subscribed'
            }
            tone={
              working.feed.subscribed && working.feed.instrumentCount
                ? 'good'
                : working.enabled
                  ? 'warn'
                  : 'neutral'
            }
            detail={
              working.feed.subscribed && !working.feed.instrumentCount
                ? 'The subscription was built and matched no instrument — the instrument master is probably empty. Refresh it from Settings.'
                : working.feed.lastResyncMs === null ||
                    working.feed.lastResyncMs === undefined
                  ? 'The subscription has not been rebuilt in this process.'
                  : `Subscription last rebuilt in ${working.feed.lastResyncMs} ms.`
            }
          />
        </Grid>
      </Grid>

      {working.enabled && !working.jobs.healthy ? (
        <Alert severity="error" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          <AlertTitle>A background job that should be running is not.</AlertTitle>
          The strategy is switched on, so its clock and its stop watcher are both
          expected to be alive. One of them is not, which means scheduled runs or
          trailing stops are silently not happening. The process-wide task table
          is on the{' '}
          <MuiLink component={RouterLink} to="/health">
            system health page
          </MuiLink>.
        </Alert>
      ) : null}

      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ display: 'block', mt: 2 }}
      >
        {working.feed.note} CPU, memory, uptime, the upstream socket, the browser
        sockets, the credentials and the schema revision are process-wide and stay
        on the <MuiLink component={RouterLink} to="/health">
            system health page
          </MuiLink> rather than
        being copied here.
      </Typography>
    </Panel>
  );
}

/** "What it will do next, and what it last did" */
function ScheduleHealth({ health }) {
  const block = health.schedule;
  const schedule = block.schedule;
  const journal = block.journal;

  // One local tick a second, so the countdowns move between the page's polls.
  // A stalled poll then shows up as a clock running past a run that never
  // happened, rather than as a frozen number that looks fine.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const countdown = (iso) => {
    if (!iso) return null;
    const target = Date.parse(iso);
    if (Number.isNaN(target)) return null;
    const seconds = Math.max(0, Math.round((target - now) / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours >= 24) return `in ${Math.floor(hours / 24)}d ${hours % 24}h`;
    if (hours) return `in ${hours}h ${minutes}m`;
    return `in ${minutes}m ${seconds % 60}s`;
  };

  return (
    <Stack spacing={2}>
      <Panel
        title="What it will do next, and what it last did"
        subtitle="The Live tab answers “what now”. This answers “is the clock sound”. Both times are the ones in force, which are not necessarily the ones the strategy ships with — the Configuration tab is where they are changed."
      >
        <Grid container spacing={2}>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Clock"
              value={
                block.running === false
                  ? 'NOT RUNNING'
                  : block.activity || 'idle — waiting for the next run'
              }
              hint="“Not running”, “idle” and “busy” are three different states. Idle is healthy."
            />
            <Field
              label="Clock checks"
              mono
              value={formatCompact(block.clockChecks)}
              hint="How many times this process's scheduler has woken up and looked at the time."
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Next analysis of stocks"
              mono
              value={
                countdown(schedule?.nextNightlyAtIst) ?? (
                  <Missing hint="No next run could be computed.">not scheduled</Missing>
                )
              }
              hint={
                schedule?.nextNightlyAtIst
                  ? `${new Date(schedule.nextNightlyAtIst).toLocaleString()} IST. It refreshes prices, decides, moves every trailing stop and writes the record. It places no order.`
                  : ''
              }
            />
            <Field
              label="Set to run at"
              mono
              value={schedule ? `${schedule.nightlyAtIst} IST` : null}
              hint={
                schedule && schedule.nightlyAtIst !== schedule.nightlyAtDefaultIst
                  ? `Changed from the shipped ${schedule.nightlyAtDefaultIst}.`
                  : 'The value this strategy ships with.'
              }
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Next order placement"
              mono
              value={
                countdown(schedule?.nextRebalanceAtIst) ?? (
                  <Missing hint="No next run could be computed.">not scheduled</Missing>
                )
              }
              hint={
                schedule?.nextRebalanceAtIst
                  ? `${new Date(schedule.nextRebalanceAtIst).toLocaleString()} IST. Weekday only — this application has no holiday list, because the trading calendar is the index's own bar dates. On an exchange holiday the countdown runs down and the run records a skipped session.`
                  : ''
              }
            />
            <Field
              label="Set to run at"
              mono
              value={schedule ? `${schedule.rebalanceAtIst} IST` : null}
              hint={
                schedule && schedule.rebalanceAtIst !== schedule.rebalanceAtDefaultIst
                  ? `Changed from the shipped ${schedule.rebalanceAtDefaultIst}.`
                  : 'The value this strategy ships with.'
              }
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Cadence"
              value={schedule?.cadence}
              hint="P18, and not editable at runtime: picking between daily and weekly is choosing a different strategy rather than configuring this one."
            />
            <Field
              label="Sessions recorded"
              mono
              value={formatQty(journal?.recordedSessions)}
              hint="Rows in the append-only decision journal. This one survives a restart."
            />
          </Grid>
        </Grid>

        <Divider sx={{ my: 2 }} />

        {/* The journal, not the in-memory list. After a restart the list below
            is empty while these are not, and a panel that showed only the list
            would say "nothing has run" about a strategy that ran last night. */}
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <LastRun label="Last analysis of stocks" record={journal?.latestAnalysis} />
          </Grid>
          <Grid item xs={12} md={6}>
            <LastRun
              label="Last order placement"
              record={journal?.latestOrderPlacement}
            />
          </Grid>
        </Grid>
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{ display: 'block', mt: 1.5 }}
        >
          {journal?.note}
        </Typography>

        {block.error ? (
          <Alert severity="error" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
            The clock's last error: {block.error}
          </Alert>
        ) : null}

        {block.closingAuction ? (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ display: 'block', mt: 2 }}
          >
            {block.closingAuction.note}
          </Typography>
        ) : null}
      </Panel>

      <MissedRuns block={block} />
      <RecentRuns block={block} />
    </Stack>
  );
}

function LastRun({ label, record }) {
  const theme = useTheme();
  if (!record) {
    return (
      <Stack spacing={0.5}>
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="body2" color="text.disabled">
          none recorded yet
        </Typography>
        <Typography variant="caption" color="text.disabled">
          The journal has no row of this kind. That is what a strategy that has
          never run looks like.
        </Typography>
      </Stack>
    );
  }
  const failed = record.status === 'FAILED';
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Stack direction="row" spacing={1} alignItems="baseline">
        <Typography
          variant="body2"
          className="numeric"
          sx={{ fontWeight: 600, color: failed ? theme.market.down : undefined }}
        >
          {record.sessionDate}
        </Typography>
        <Chip
          size="small"
          label={record.status}
          sx={{
            height: 18,
            bgcolor: failed ? 'error.main' : 'action.selected',
            color: failed ? 'error.contrastText' : 'text.secondary',
          }}
        />
      </Stack>
      <Typography variant="caption" color="text.disabled">
        {record.message || 'no message recorded'}
      </Typography>
    </Stack>
  );
}

/**
 * Missed runs, moved here off the Live tab.
 *
 * A gap in the journal is a health fact, and it was the loudest thing on the
 * Live tab despite usually being historical. Reported, never repaired: a
 * decision recorded days late, on bars that may since have been restated, would
 * be a record of a decision nobody took.
 */
function MissedRuns({ block }) {
  const missed = block.missedRunCount ?? 0;
  return (
    <Panel
      title="Sessions with no decision record"
      subtitle="Detected against the regime index's own bar dates, and reported rather than re-decided. Trailing stops were not recomputed on these sessions."
    >
      {missed === 0 ? (
        <Alert severity="success" icon={<InfoOutlinedIcon />}>
          No gaps.{' '}
          {block.checkedForMissedAtIst
            ? `Last checked ${new Date(block.checkedForMissedAtIst).toLocaleString()}.`
            : 'This process has not run the check yet — which is not the same as there being no gaps.'}
        </Alert>
      ) : (
        <Alert severity="warning" icon={<WarningAmberIcon />}>
          <AlertTitle>
            {missed} session{missed === 1 ? '' : 's'} with no record
          </AlertTitle>
          {(block.missedRuns ?? []).map((entry) => (
            <Typography
              key={`${entry.kind}-${entry.sessions.join()}`}
              variant="body2"
              sx={{ mb: 0.5 }}
            >
              <strong>{RUN_LABELS[entry.kind] ?? entry.kind}</strong> — {entry.count}{' '}
              session{entry.count === 1 ? '' : 's'}: {entry.sessions.join(', ')}
            </Typography>
          ))}
          {block.checkedForMissedAtIst ? (
            <Typography variant="caption" color="text.secondary">
              Last checked {new Date(block.checkedForMissedAtIst).toLocaleString()}.
            </Typography>
          ) : null}
        </Alert>
      )}
    </Panel>
  );
}

/**
 * The scheduler's in-memory activity log, moved here off the Live tab.
 *
 * Diagnostics, not a journal: it lives in the process and starts empty after a
 * restart. The panel says so, because a twenty-row table that is silently a
 * different kind of record from the one above it is how the two get confused.
 */
function RecentRuns({ block }) {
  const rows = block.recent ?? [];
  return (
    <Panel title="Recent scheduled runs" subtitle={block.recentNote}>
      {!rows.length ? (
        <Alert severity="info">
          No scheduled job has run since this process started.
        </Alert>
      ) : (
        <TableContainer>
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
                  <TableCell>{RUN_LABELS[run.kind] ?? run.kind}</TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={run.ok ? 'ok' : 'FAILED'}
                      sx={{
                        height: 18,
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
      )}
    </Panel>
  );
}

/** "The data it decides on" */
function DataHealth({ health }) {
  const theme = useTheme();
  const data = health.data;
  const bars = data.bars;
  const stale = data.staleness;
  const lagging = data.laggingSymbols;
  const universe = data.universe;

  const staleTone =
    stale.wouldRefuse === null || stale.wouldRefuse === undefined
      ? 'neutral'
      : stale.wouldRefuse
        ? 'bad'
        : 'good';

  return (
    <Panel
      title="The data it decides on"
      subtitle="The most likely thing to break quietly, because it depends on an overnight job and an external vendor. Every bar here was fetched whole from Dhan's historical endpoint; nothing in this table comes from the live feed."
    >
      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} md={3}>
          <Field label="Symbols with bars" mono value={formatQty(bars.symbols)} />
          <Field label="Bars stored" mono value={formatCompact(bars.rows)} />
          <Field
            label="Oldest session"
            mono
            value={bars.oldestSession ?? <Missing>none</Missing>}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Verdict
            label="Newest session"
            state={stale.newestSession ?? 'no bars stored'}
            tone={staleTone}
            detail={
              stale.daysBehind === null || stale.daysBehind === undefined
                ? 'Nothing is stored, so nothing can be stale. Run the bootstrap import.'
                : `${stale.daysBehind} calendar day(s) ago, against a limit of ${stale.limitDays}.`
            }
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Field
            label="Universe configured"
            mono
            value={formatQty(universe.configured)}
            hint={universe.name ? `From conf/universes/${universe.name}.csv.` : ''}
          />
          <Field
            label="Resolved in the master"
            mono
            value={
              universe.resolved === null || universe.resolved === undefined ? (
                <Missing />
              ) : (
                formatQty(universe.resolved)
              )
            }
          />
          <Field
            label="Unresolved"
            mono
            color={universe.unresolvedCount ? theme.palette.warning.main : undefined}
            value={
              universe.unresolvedCount === null ||
              universe.unresolvedCount === undefined ? (
                <Missing />
              ) : (
                formatQty(universe.unresolvedCount)
              )
            }
            hint={
              universe.unresolved?.length
                ? `No active row in the instrument master: ${universe.unresolved.join(', ')}${universe.unresolvedCount > universe.unresolved.length ? ', …' : ''}`
                : 'Every configured symbol has an active row in the instrument master.'
            }
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Field
            label="F&O-eligible"
            mono
            value={formatQty(universe.fnoEligible)}
            hint="Derived from the master's own FUTSTK rows, never configured. These are the names whose continuous trading ends at 15:15 rather than 15:30."
          />
          <Field
            label="Instrument master rows"
            mono
            value={formatQty(data.instrumentMaster.activeRowsInSegment)}
            hint={`Active rows in ${data.segment}. ${formatQty(data.instrumentMaster.activeRows)} active rows in total, across every segment.`}
          />
          <Field
            label="Master last refreshed"
            value={
              data.instrumentMaster.lastRefreshedAt ? (
                formatTime(data.instrumentMaster.lastRefreshedAt)
              ) : (
                <Missing>never</Missing>
              )
            }
          />
        </Grid>
      </Grid>

      {stale.wouldRefuse ? (
        <Alert severity="error" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          <AlertTitle>The next order placement would refuse.</AlertTitle>
          {stale.refusalReason}
        </Alert>
      ) : null}

      {universe.error ? (
        <Alert severity="warning" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
          The universe could not be resolved against the instrument master:{' '}
          {universe.error}
        </Alert>
      ) : null}

      <LaggingSymbols lagging={lagging} />

      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ display: 'block', mt: 2 }}
      >
        {stale.note} {universe.note}
      </Typography>
      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ display: 'block', mt: 1 }}
      >
        {data.refreshHistoryNote}
      </Typography>
    </Panel>
  );
}

/**
 * Symbols whose newest bar is behind the rest of the table.
 *
 * These are the ones that silently drop out of the ranking: a symbol with no
 * bar on the session is SKIPPED rather than forward-filled, so it stops being
 * a candidate without anything failing.
 */
function LaggingSymbols({ lagging }) {
  if (lagging.count === null || lagging.count === undefined) {
    return (
      <Alert severity="info" sx={{ mt: 2 }}>
        {lagging.note}
      </Alert>
    );
  }
  if (!lagging.count && !lagging.missingEntirely) {
    return (
      <Alert severity="success" sx={{ mt: 2 }} icon={<InfoOutlinedIcon />}>
        Every symbol with stored bars is up to the newest session.
      </Alert>
    );
  }
  return (
    <Box sx={{ mt: 2 }}>
      <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mb: 1 }}>
        <AlertTitle>
          {lagging.count} symbol{lagging.count === 1 ? '' : 's'} behind the newest
          session
          {lagging.missingEntirely
            ? `, and ${lagging.missingEntirely} with no bars at all`
            : ''}
        </AlertTitle>
        {lagging.note}
      </Alert>
      {lagging.behind.length ? (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbol</TableCell>
                <TableCell>Newest bar</TableCell>
                <TableCell align="right">Sessions behind</TableCell>
                <TableCell align="right">Days behind</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {lagging.behind.map((row) => (
                <TableRow key={row.symbol}>
                  <TableCell sx={{ fontWeight: 500 }}>{row.symbol}</TableCell>
                  <TableCell className="numeric">{row.lastSession}</TableCell>
                  <TableCell align="right" className="numeric">
                    {row.sessionsBehind === null || row.sessionsBehind === undefined ? (
                      <Missing hint="This symbol fell behind before the window of the index calendar that was read." />
                    ) : (
                      `${row.sessionsBehindIsAtLeast ? '≥ ' : ''}${row.sessionsBehind}`
                    )}
                  </TableCell>
                  <TableCell align="right" className="numeric">
                    {row.daysBehind}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      ) : null}
      {lagging.count > lagging.behind.length ? (
        <Typography variant="caption" color="text.disabled">
          Showing the {lagging.behind.length} furthest behind of {lagging.count}.
        </Typography>
      ) : null}
    </Box>
  );
}

/** "What it holds, and what it is watching" */
function HoldingsHealth({ health }) {
  const theme = useTheme();
  const holdings = health.holdings;
  const stops = holdings.stops;
  const watcher = holdings.watcher;
  const money = holdings.money;

  return (
    <Stack spacing={2}>
      <Panel
        title="What it holds, and what it is watching"
        subtitle="Four money figures, never one. Blocked margin is a configured approximation, and equity is withheld rather than zeroed when an open position has no live mark."
      >
        <Grid container spacing={2}>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Open positions"
              mono
              value={
                holdings.positions === null || holdings.positions === undefined ? (
                  <Missing hint={holdings.positionsNote}>no portfolio</Missing>
                ) : (
                  formatQty(holdings.positions)
                )
              }
            />
            <Field
              label="Without a live mark"
              mono
              value={
                holdings.unmarkedPositions === null ||
                holdings.unmarkedPositions === undefined ? (
                  <Missing>—</Missing>
                ) : (
                  formatQty(holdings.unmarkedPositions)
                )
              }
              hint="A position with no live price. Its unrealised P&L and the portfolio's equity are withheld rather than shown as zero."
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Active stops"
              mono
              value={formatQty(stops.active)}
              hint="Chandelier trailing stops on open positions."
            />
            <Field
              label="Without a level yet"
              mono
              value={formatQty(stops.withoutLevel)}
              color={stops.withoutLevel ? theme.palette.warning.main : undefined}
              hint="A stop ROW exists from entry; a stop LEVEL needs an ATR14. Only the second one protects anything, and the next analysis run sets it."
            />
            <Field
              label="Closed"
              mono
              value={formatQty(stops.closed)}
              hint="Positions that have left the book through a stop row."
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Triggered, waiting"
              mono
              value={formatQty(stops.triggeredWaiting)}
              color={stops.triggeredWaiting ? theme.palette.warning.main : undefined}
              hint={stops.triggeredNote}
            />
            <Field
              label="Nearest stop"
              mono
              value={
                watcher.nearestPercent === null || watcher.nearestPercent === undefined ? (
                  <Missing hint="Nothing measurable: no stop is being watched, or no watched position has a live mark. A book with no marks does not have a nearest stop of 0%." />
                ) : (
                  `${watcher.nearestSymbol} ${watcher.nearestPercent.toFixed(2)}%`
                )
              }
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            {money ? (
              <>
                <Field label="Cash" mono value={`₹${money.cash}`} />
                <Field
                  label="Blocked margin"
                  mono
                  value={`₹${money.blockedMargin} (estimate)`}
                  hint="A configured approximation, not what a broker would hold."
                />
                <Field label="Available" mono value={`₹${money.available}`} />
                <Field
                  label="Equity"
                  mono
                  value={
                    money.equity === null ? (
                      <Missing hint={`Withheld because ${money.unmarkedPositions} open position(s) have no live mark. Valuing an unmarked position at zero would be a lie.`}>
                        no mark
                      </Missing>
                    ) : (
                      `₹${money.equity}`
                    )
                  }
                />
              </>
            ) : (
              <Alert severity="info">
                {holdings.positionsNote ??
                  'Pick a portfolio in the header to see its money.'}
              </Alert>
            )}
          </Grid>
        </Grid>
      </Panel>

      <Panel
        title="The stop watcher"
        subtitle="Its own task, polling off the tick path. It acts only while the market is open, never on a missing price, and never inside the Closing Auction Session for an F&O-eligible name."
      >
        <Grid container spacing={2}>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="State"
              value={
                watcher.enabled === false
                  ? 'switched off in configuration'
                  : watcher.running === false
                    ? 'NOT RUNNING'
                    : 'running'
              }
              color={
                watcher.enabled === false || watcher.running === false
                  ? theme.market.down
                  : theme.market.up
              }
            />
            <Field label="Poll interval" mono value={`${watcher.intervalMs} ms`} />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field
              label="Watching"
              mono
              value={
                watcher.watching === null || watcher.watching === undefined ? (
                  <Missing hint="The watcher has completed no pass since this process started. That is a different state from watching nothing.">
                    no pass yet
                  </Missing>
                ) : (
                  formatQty(watcher.watching)
                )
              }
            />
            <Field
              label="Last pass"
              value={
                watcher.lastPassAtIst ? (
                  new Date(watcher.lastPassAtIst).toLocaleTimeString()
                ) : (
                  <Missing>none since restart</Missing>
                )
              }
            />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field label="Passes" mono value={formatCompact(watcher.passes)} />
            <Field label="Stops triggered" mono value={formatQty(watcher.triggered)} />
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Field label="Exits placed" mono value={formatQty(watcher.exitsPlaced)} />
            <Field
              label="Deferred to auction"
              mono
              value={formatQty(watcher.deferredToAuction)}
              color={watcher.deferredToAuction ? theme.palette.warning.main : undefined}
            />
          </Grid>
        </Grid>

        {watcher.error ? (
          <Alert severity="error" sx={{ mt: 2 }} icon={<WarningAmberIcon />}>
            The watcher's last error: {watcher.error}
          </Alert>
        ) : null}

        {/* A process-wide number under a strategy heading, said out loud. */}
        <Typography
          variant="caption"
          color="text.disabled"
          sx={{ display: 'block', mt: 2 }}
        >
          <strong>Passes, stops triggered, exits placed and deferred to auction:</strong>{' '}
          {watcher.countersNote}
        </Typography>
      </Panel>
    </Stack>
  );
}

/** "The rules it is running under" */
function RulesHealth({ health }) {
  const theme = useTheme();
  const rules = health.rules;

  const author = (row) => {
    if (!row.everChanged) return 'never changed';
    const when = row.updatedAt ? new Date(row.updatedAt).toLocaleString() : 'unknown time';
    return `${row.updatedBy} · ${when}`;
  };

  return (
    <Panel
      title="The rules it is running under"
      subtitle={
        <>
          What the strategy ships with, beside what is actually in force. Read-only
          here — change it on the{' '}
          <MuiLink component={RouterLink} to="/swing?tab=configuration">
            Configuration tab
          </MuiLink>.
        </>
      }
    >
      {rules.contradiction ? (
        <Alert severity="error" sx={{ mb: 2 }} icon={<WarningAmberIcon />}>
          {rules.contradiction}
        </Alert>
      ) : null}

      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Rule</TableCell>
              <TableCell>Ships as</TableCell>
              <TableCell>In force</TableCell>
              <TableCell>Last changed</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rules.policies.map((row) => {
              const diverged = row.enforced !== row.default;
              return (
                <TableRow key={row.key} hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {row.label}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {row.description}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.disabled">
                      {row.default ? row.onLabel : row.offLabel}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={row.enforced ? row.onLabel : row.offLabel}
                      sx={{
                        height: 20,
                        fontWeight: 600,
                        bgcolor: diverged ? 'warning.main' : 'action.selected',
                        color: diverged ? 'warning.contrastText' : 'text.secondary',
                      }}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {author(row)}
                    </Typography>
                  </TableCell>
                </TableRow>
              );
            })}
            {rules.settings.map((row) => {
              const diverged = row.value !== row.default;
              return (
                <TableRow key={row.key} hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      {row.label}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {row.description}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.disabled" className="numeric">
                      {row.default} IST
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography
                      variant="body2"
                      className="numeric"
                      sx={{
                        fontWeight: 600,
                        color: diverged ? theme.palette.warning.main : undefined,
                      }}
                    >
                      {row.value} IST
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {author(row)}
                    </Typography>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>

      <Divider sx={{ my: 2 }} />

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6}>
          <Field
            label="Switched on"
            value={health.working.enabled ? 'ON' : 'OFF'}
            hint="Whether it computes, decides and records at all."
          />
          <Field label="Last changed" value={author(rules.switches.enabled)} />
        </Grid>
        <Grid item xs={12} sm={6}>
          <Field
            label="Auto trade"
            value={health.working.armed ? 'ON' : 'OFF'}
            hint="Whether it may submit an order of its own accord. It lives on Strategies & Features, with the strategy's running state, because it is the one control that lets the software spend money on its own."
          />
          <Field label="Last changed" value={author(rules.switches.armed)} />
        </Grid>
      </Grid>

      <Alert severity="info" sx={{ mt: 2 }} icon={<InfoOutlinedIcon />}>
        {rules.auditNote}
      </Alert>

      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ display: 'block', mt: 1.5 }}
      >
        {rules.notEditableNote}
      </Typography>
    </Panel>
  );
}

/** "Recent problems" */
function Problems({ health }) {
  const theme = useTheme();
  const problems = health.problems;

  if (!problems.available) {
    return (
      <Panel title="Recent problems">
        <Alert severity="warning">{problems.note}</Alert>
      </Panel>
    );
  }

  const records = problems.records ?? [];
  return (
    <Panel
      title="Recent problems"
      subtitle={`${problems.note} Loggers: ${problems.loggerPrefixes.join('.*, ')}.*`}
    >
      {!records.length ? (
        <Alert severity="success" icon={<InfoOutlinedIcon />}>
          No warning or error from this strategy's components is in the buffer.
        </Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>When</TableCell>
                <TableCell>Level</TableCell>
                <TableCell>Component</TableCell>
                <TableCell>Message</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {records.map((record, index) => (
                <TableRow key={`${record.timestampMs}-${index}`}>
                  <TableCell className="numeric" sx={{ whiteSpace: 'nowrap' }}>
                    {new Date(record.timestampMs).toLocaleString()}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={record.level}
                      sx={{
                        height: 18,
                        bgcolor:
                          record.level === 'ERROR' || record.level === 'CRITICAL'
                            ? 'error.main'
                            : 'warning.main',
                        color:
                          record.level === 'ERROR' || record.level === 'CRITICAL'
                            ? 'error.contrastText'
                            : 'warning.contrastText',
                        fontWeight: 600,
                      }}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {record.logger}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography
                      variant="caption"
                      sx={{
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        color: theme.palette.text.primary,
                      }}
                    >
                      {record.message}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ display: 'block', mt: 1.5 }}
      >
        {problems.bufferNote}{' '}
        {problems.totalSeen
          ? `${problems.totalSeen} record(s) have passed through the buffer since this process started, across every component.`
          : null}
      </Typography>
    </Panel>
  );
}

export default function SwingHealth({ health, error, isAdmin }) {
  if (!isAdmin) {
    return (
      <Alert severity="info" icon={<InfoOutlinedIcon />}>
        <AlertTitle>Administrators only</AlertTitle>
        This tab shows live machinery state and recent log records, which is why
        it is restricted the same way the system health page is. The rest of this
        page — what the strategy decided, what it holds and how it has done — is
        open to you on the other tabs.
      </Alert>
    );
  }

  if (error) {
    return (
      <Alert severity="error" icon={<WarningAmberIcon />}>
        <AlertTitle>The health payload could not be read.</AlertTitle>
        {error}
      </Alert>
    );
  }

  if (!health) {
    // Before the first read we know NOTHING, which is not the same as "off",
    // "closed" or "zero". Rendering the not-yet-loaded state as a healthy
    // strategy would be a confident answer to a question nobody has asked the
    // server yet.
    return (
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 3 }}>
        <CircularProgress size={16} />
        <Typography variant="body2" color="text.secondary">
          Reading what this strategy is doing…
        </Typography>
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <WorkingNow health={health} />
      <ScheduleHealth health={health} />
      <DataHealth health={health} />
      <HoldingsHealth health={health} />
      <RulesHealth health={health} />
      <Problems health={health} />
    </Stack>
  );
}
