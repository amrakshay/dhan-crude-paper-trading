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
  Tooltip,
  Typography,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { useTheme } from '@mui/material/styles';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { formatPrice } from '../utils/format';

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

/** The stored run kind in the words the page uses, the same translation the
 *  page itself makes. The DATABASE keeps SCAN and EXIT; renaming stored values
 *  would rewrite history, so the translation lives at the edge. */
const RUN_LABELS = {
  SCAN: 'Afternoon scan',
  EXIT: 'Morning exit',
  MANUAL: 'Manual run',
};

export default function BtstHealth({ health: payload, error }) {
  // NO POLL OF ITS OWN. The page already polls at 10 s and fetches this on the
  // same cadence, because a page that reports on load must not be a load
  // source. Its failure arrives here as `error` and is kept off the page's own
  // error state, so a hiccup on this admin-only read cannot blank the Live tab.
  if (error) return <Alert severity="error">{error}</Alert>;

  if (!payload) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  const exit = payload?.exit || {};
  const working = payload?.working || {};
  const feed = payload?.feed || {};
  const data = payload?.data || {};
  const money = payload?.money || {};
  const schedule = payload?.schedule || {};
  const rules = payload?.rules || {};

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
        {/* EVERY open position with when its exit is due — not only the
            overdue ones. The Live tab's book says what is held and why it was
            bought; this says whether each is still inside the window it is
            meant to leave in, which is the only question this tab exists to
            answer. */}
        {exit.holdings?.length ? (
          <TableContainer sx={{ mt: 1.5 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Symbol</TableCell>
                  <TableCell align="right">Qty</TableCell>
                  <TableCell>Entered</TableCell>
                  <TableCell>Due out</TableCell>
                  <TableCell>State</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {exit.holdings.map((one) => (
                  <TableRow key={`${one.symbol}-${one.entrySessionDate}`}>
                    <TableCell>{one.symbol}</TableCell>
                    <TableCell align="right" className="numeric">
                      {one.quantity}
                    </TableCell>
                    <TableCell>{one.entrySessionDate}</TableCell>
                    <TableCell className="numeric">
                      {new Date(one.dueAtIst).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      {one.overdue ? (
                        <Stack spacing={0.25}>
                          <Chip size="small" color="error" label={`${one.minutesLate} min late`} />
                          <Typography variant="caption">
                            {one.exitReason || one.exitStatus}
                          </Typography>
                        </Stack>
                      ) : (
                        <Typography variant="caption" color="text.secondary">
                          held, not yet due
                        </Typography>
                      )}
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

      {/* CAN IT AFFORD TO TRADE? B12 sizes every entry as total equity over
          the slot count, and the funds check runs again at the fill — so a
          withheld equity figure means the next scan buys nothing, which is
          worth knowing at 15:19 rather than at 15:21. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Verdict tone={money.tone} title="Can it size an entry?" text={money.verdict} />
        {money.balance ? (
          <Stack direction="row" spacing={3} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
            <Figure label="Cash" value={formatPrice(money.balance.cash)} />
            <Figure
              label="Blocked margin (estimate)"
              value={formatPrice(money.balance.blockedMargin)}
            />
            <Figure label="Available" value={formatPrice(money.balance.available)} />
            <Figure
              label="Equity"
              value={
                money.balance.equity === null || money.balance.equity === undefined
                  ? 'no mark'
                  : formatPrice(money.balance.equity)
              }
              bad={money.balance.equity === null || money.balance.equity === undefined}
            />
            <Figure label="Slots" value={money.slots} />
          </Stack>
        ) : null}
        {money.note ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            {money.note}
          </Typography>
        ) : null}
      </Paper>

      {/* WHAT IT HAS ACTUALLY RUN. Two lists, because they answer different
          questions: the scheduler's is what THIS PROCESS has done and is empty
          after a restart, and the missed list is holes in the journal, which
          is not. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          Recent scheduled runs
        </Typography>
        {(schedule.recent || []).length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            This process has run no scheduled job for this strategy since it
            started. That is not the same as none having happened — the decision
            history on the Live tab is the durable record.
          </Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>When</TableCell>
                  <TableCell>Run</TableCell>
                  <TableCell>Outcome</TableCell>
                  <TableCell>Detail</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {(schedule.recent || []).slice(0, 20).map((one, index) => (
                  <TableRow key={`${one.atIst}-${index}`}>
                    <TableCell className="numeric">
                      {new Date(one.atIst).toLocaleString()}
                    </TableCell>
                    <TableCell>{RUN_LABELS[one.kind] || one.kind}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={one.ok ? 'ok' : 'FAILED'}
                        color={one.ok ? 'default' : 'error'}
                      />
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption">{one.detail || '—'}</Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}

        {(schedule.missedRuns || []).length ? (
          <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mt: 1.5 }}>
            <Stack spacing={0.5}>
              {schedule.missedRuns.map((entry) => (
                <Typography key={entry.kind} variant="body2">
                  <strong>{RUN_LABELS[entry.kind] || entry.kind}</strong>:{' '}
                  {entry.count} session(s) with no record —{' '}
                  <span className="numeric">{(entry.sessions || []).join(', ')}</span>
                </Typography>
              ))}
              <Typography variant="caption">
                Reported, never silently re-decided later. A missed SCAN costs
                that session's signals; a missed EXIT is the one that costs the
                thesis.
              </Typography>
            </Stack>
          </Alert>
        ) : null}

        {schedule.note ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            {schedule.note}
          </Typography>
        ) : null}
      </Paper>

      {/* WHICH RULES ARE IN FORCE, and which times. Both carry the shipped
          default beside the value actually in use, so a switch somebody moved
          is visible as a move rather than as the way it has always been. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          The rules it is running under
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Rule</TableCell>
                <TableCell>Shipped</TableCell>
                <TableCell>In force</TableCell>
                <TableCell>Moved?</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(rules.policies || []).map((one) => (
                <TableRow key={one.key}>
                  <TableCell>
                    <Tooltip title={one.description || ''}>
                      <span>{one.label}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {one.default ? one.onLabel : one.offLabel}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={one.enforced ? one.onLabel : one.offLabel}
                      color={one.enforced ? 'default' : 'warning'}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {one.overridden ? 'yes' : 'no'}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
              {(rules.settings || []).map((one) => (
                <TableRow key={one.key}>
                  <TableCell>
                    <Tooltip title={one.description || ''}>
                      <span>{one.label}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary" className="numeric">
                      {one.default}
                    </Typography>
                  </TableCell>
                  <TableCell className="numeric">{one.value}</TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {one.overridden ? 'yes' : 'no'}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        {rules.note ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            {rules.note}
          </Typography>
        ) : null}
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
