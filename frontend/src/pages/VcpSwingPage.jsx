import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Grid,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { vcpApi } from '../api/vcp';
import { chipTone } from '../theme/chipTone';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { formatIstDateTime, formatPercent, formatPrice, formatQty } from '../utils/format';

/**
 * NSE VCP Swing.
 *
 * Buys a Nifty 500 name the morning after a volatility contraction pattern is
 * first seen forming, and holds it on two stops in sequence: the base's final
 * low until it breaks out, then the first daily close under the 50 EMA.
 *
 * The honesty rules of the other strategy pages apply (frontend CLAUDE.md
 * section 3): an unmeasured figure shows as a dash, never 0; "no candidates
 * tonight" is the NORMAL state at about one signal a week; and the page says in
 * words when it is not armed, rather than letting a list of SKIPPED rows imply
 * a broken strategy.
 */
const TABS = [
  { key: 'today', label: 'Today' },
  { key: 'positions', label: 'Positions' },
  { key: 'decisions', label: 'Decisions' },
  { key: 'signals', label: 'Signals' },
  { key: 'journal', label: 'Run journal' },
  { key: 'rules', label: 'Rules' },
];

const num = (value) => (value === null || value === undefined ? null : Number(value));
const pct = (value) => (value === null || value === undefined ? '—' : formatPercent(value * 100));
const ratio = (value) => (value === null || value === undefined ? '—' : formatQty(Math.round(value * 100) / 100));

// What each recorded state MEANS, in chipTone's vocabulary (frontend CLAUDE.md 5f):
// the theme gives every chip a background, so `color=` alone is unreadable.
const TONES = {
  COMPLETED: 'done', TAKEN: 'done', BOUGHT: 'done', SOLD: 'done',
  CANDIDATE: 'pending', RUNNING: 'pending', FLAGGED: 'pending',
  FAILED: 'problem', HELD: 'problem',
};

function ToneChip({ label, tone }) {
  const theme = useTheme();
  return <Chip size="small" variant="outlined" label={label}
    sx={chipTone(theme, tone || TONES[label] || 'neutral')} />;
}

export default function VcpSwingPage() {
  const { activeId } = useActivePortfolio();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const requested = searchParams.get('tab') || 'today';
  const tab = TABS.some((one) => one.key === requested) ? requested : 'today';

  const load = useCallback(async () => {
    try {
      setData(await vcpApi.overview(activeId));
      setError(null);
    } catch (err) {
      setError(err?.message || 'Could not load the VCP journal.');
    }
  }, [activeId]);

  useEffect(() => {
    load();
    // Positions carry a live price and distance to the stop; the rest moves
    // twice a day. The Positions cadence elsewhere in the app is 5 s.
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const open = useMemo(
    () => (data?.positions || []).filter((p) => p.status === 'OPEN'),
    [data],
  );

  if (!data && !error) {
    return (
      <Box sx={{ p: 4, textAlign: 'center' }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ p: { xs: 1, sm: 2 } }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ sm: 'center' }}
        spacing={1}
        sx={{ mb: 2 }}
      >
        <Box>
          <Typography variant="h5">{data?.label || 'NSE VCP Swing'}</Typography>
          <Typography variant="body2" color="text.secondary">
            Buys the morning after a VCP base is first seen forming. Stop at the base&apos;s
            final low; after a breakout, out on the first close under the 50 EMA.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <StateChip on={data?.enabled} onLabel="ENABLED" offLabel="OFF"
            help="Whether it scans and journals at all. Switched on the Strategies page." />
          <StateChip on={data?.armed} onLabel="ARMED" offLabel="NOT ARMED"
            help="Whether it may place an order. Unarmed, it decides the same and places nothing." />
        </Stack>
      </Stack>

      {error ? <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert> : null}

      {data && !data.bootstrap.ready ? (
        <Alert severity="warning" sx={{ mb: 2 }}>
          The scan has no memory yet ({data.bootstrap.scanned} of {data.bootstrap.universe}{' '}
          symbols). Run the one-off bootstrap before enabling it, from <code>backend/</code>:{' '}
          <code>{data.bootstrap.command}</code>. Until then the nightly refuses to run rather
          than mistake every base already in progress for a new one.
        </Alert>
      ) : null}

      {data && data.portfolios.length === 0 ? (
        <Alert severity="info" sx={{ mb: 2 }}>
          No portfolio runs this strategy yet, so the morning run has nothing to trade.
          Attach it to one on the Portfolios page.
        </Alert>
      ) : null}

      <Alert severity="info" sx={{ mb: 2 }}>
        Paper only, and no track record yet. The backtest this mirrors (daily scan, next-open
        fills, real charges) measured about 18.7% a year with a worst fall of about −22%, on
        today&apos;s Nifty 500 held back to 2016 — survivorship bias this record is free of.
        Roughly 78% of trades lose; the profit is in a few large winners.
      </Alert>

      <Tabs
        value={tab}
        onChange={(_e, value) => setSearchParams({ tab: value })}
        variant="scrollable"
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
      >
        {TABS.map((one) => (
          <Tab key={one.key} value={one.key}
            label={one.key === 'positions' ? `Positions (${open.length})` : one.label} />
        ))}
      </Tabs>

      {data && tab === 'today' ? <TodayTab data={data} /> : null}
      {data && tab === 'positions' ? <PositionsTab positions={data.positions} /> : null}
      {data && tab === 'decisions' ? <DecisionsTab decisions={data.decisions} /> : null}
      {data && tab === 'signals' ? <SignalsTable signals={data.signals} /> : null}
      {data && tab === 'journal' ? <JournalTab journal={data.journal} /> : null}
      {data && tab === 'rules' ? <RulesTab data={data} /> : null}
    </Box>
  );
}

function StateChip({ on, onLabel, offLabel, help }) {
  return (
    <Tooltip title={help}>
      <span><ToneChip label={on ? onLabel : offLabel} tone={on ? 'done' : 'neutral'} /></span>
    </Tooltip>
  );
}

function Metric({ label, value, hint }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h6">{value}</Typography>
      {hint ? <Typography variant="caption" color="text.secondary">{hint}</Typography> : null}
    </Box>
  );
}

function RunLine({ label, run }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      {run ? (
        <>
          <Typography variant="body2">
            <ToneChip label={run.status} />{' '}
            {formatIstDateTime(run.startedAt)} — session {run.sessionDate}
          </Typography>
          <Typography variant="body2" color="text.secondary">{run.message}</Typography>
        </>
      ) : (
        <Typography variant="body2" color="text.secondary">Has not run yet.</Typography>
      )}
    </Box>
  );
}

function TodayTab({ data }) {
  const { candidates, balance } = data;
  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Grid container spacing={2}>
          <Grid item xs={6} md={3}>
            <Metric label="Available cash" value={balance ? formatPrice(num(balance.available)) : '—'}
              hint={`₹${formatQty(num(data.parameters.positionRupees))} a position`} />
          </Grid>
          <Grid item xs={6} md={3}>
            <Metric label="Equity" value={balance ? formatPrice(num(balance.equity)) : '—'}
              hint={balance && balance.equity === null ? 'withheld: a position has no live price' : null} />
          </Grid>
          <Grid item xs={6} md={3}>
            <Metric label="Analysis" value={`${data.schedule.nightlyAtIst} IST`} hint="outside the session" />
          </Grid>
          <Grid item xs={6} md={3}>
            <Metric label="Orders" value={`${data.schedule.morningAtIst} IST`} hint="at the open" />
          </Grid>
        </Grid>
      </Paper>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={1.5}>
          <RunLine label="Last nightly scan" run={data.lastNightly} />
          <RunLine label="Last morning run" run={data.lastMorning} />
        </Stack>
      </Paper>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          {candidates.sessionDate
            ? `Bases seen on ${candidates.sessionDate} — candidates for the open on ${candidates.buyOn}`
            : 'No session decided yet'}
        </Typography>
        {!candidates.sessionDate ? (
          <Typography variant="body2" color="text.secondary">
            No scan has completed, so there is nothing to report either way.
          </Typography>
        ) : candidates.signals.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            Nothing new. At about one signal a week across the Nifty 500, this is the usual answer.
          </Typography>
        ) : (
          <SignalsTable signals={candidates.signals} dense />
        )}
      </Paper>
    </Stack>
  );
}

function SignalsTable({ signals, dense }) {
  if (!signals.length) {
    return <Typography variant="body2" color="text.secondary">No signals recorded yet.</Typography>;
  }
  return (
    <TableContainer component={dense ? Box : Paper} variant={dense ? undefined : 'outlined'}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Seen</TableCell>
            <TableCell>Symbol</TableCell>
            <TableCell>Footprint</TableCell>
            <TableCell align="right">Close</TableCell>
            <TableCell align="right">Pivot</TableCell>
            <TableCell align="right">Stop</TableCell>
            <TableCell align="right">Stop / ATR</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Why</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {signals.map((s) => (
            <TableRow key={s.id}>
              <TableCell>{s.seenDate}</TableCell>
              <TableCell>{s.symbol}</TableCell>
              <TableCell>{s.variant}</TableCell>
              <TableCell align="right">{formatPrice(num(s.close))}</TableCell>
              <TableCell align="right">{formatPrice(num(s.pivot))}</TableCell>
              <TableCell align="right">{formatPrice(num(s.stop))}</TableCell>
              <TableCell align="right">{ratio(s.stopAtr)}</TableCell>
              <TableCell><ToneChip label={s.status} /></TableCell>
              <TableCell sx={{ maxWidth: 380 }}><Typography variant="caption">{s.reason}</Typography></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function PositionsTab({ positions }) {
  if (!positions.length) {
    return <Typography variant="body2" color="text.secondary">No positions yet.</Typography>;
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Symbol</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Phase</TableCell>
            <TableCell>Entered</TableCell>
            <TableCell align="right">Qty</TableCell>
            <TableCell align="right">Entry</TableCell>
            <TableCell align="right">Last</TableCell>
            <TableCell align="right">Stop</TableCell>
            <TableCell align="right">To stop</TableCell>
            <TableCell align="right">50 EMA</TableCell>
            <TableCell align="right">Unrealised</TableCell>
            <TableCell>Exit</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {positions.map((p) => (
            <TableRow key={p.id}>
              <TableCell>{p.symbol}</TableCell>
              <TableCell>{p.status}</TableCell>
              <TableCell>
                <Tooltip title={p.phase === 'BROKEN_OUT'
                  ? `Broke out ${p.breakoutDate}: exits on the first close under the 50 EMA`
                  : 'In the base: exits at the final contraction low'}>
                  <span>{p.phase === 'BROKEN_OUT' ? 'Broken out' : 'Base'}</span>
                </Tooltip>
              </TableCell>
              <TableCell>{p.entryDate}</TableCell>
              <TableCell align="right">{formatQty(p.quantity)}</TableCell>
              <TableCell align="right">{formatPrice(num(p.entryPrice))}</TableCell>
              <TableCell align="right">{formatPrice(num(p.lastPrice))}</TableCell>
              <TableCell align="right">{formatPrice(num(p.stop))}</TableCell>
              <TableCell align="right">{p.status === 'OPEN' ? pct(p.toStopPct) : '—'}</TableCell>
              <TableCell align="right">{formatPrice(num(p.lastEma))}</TableCell>
              <TableCell align="right">{p.status === 'OPEN' ? formatPrice(p.unrealisedPnl) : '—'}</TableCell>
              <TableCell>
                {p.status === 'OPEN'
                  ? (p.exitDue ? <ToneChip label={`${p.exitDue} at next open`} tone="pending" /> : '—')
                  : `${p.exitKind || ''} ${formatPrice(num(p.exitPrice))}`}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function DecisionsTab({ decisions }) {
  if (!decisions.length) {
    return <Typography variant="body2" color="text.secondary">No decisions yet.</Typography>;
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When</TableCell>
            <TableCell>Symbol</TableCell>
            <TableCell>Action</TableCell>
            <TableCell align="right">Qty</TableCell>
            <TableCell align="right">Price</TableCell>
            <TableCell>Why</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {decisions.map((d) => (
            <TableRow key={d.id}>
              <TableCell>{formatIstDateTime(d.at)}</TableCell>
              <TableCell>{d.symbol}</TableCell>
              <TableCell><ToneChip label={d.action} /></TableCell>
              <TableCell align="right">{formatQty(d.quantity)}</TableCell>
              <TableCell align="right">{formatPrice(num(d.price))}</TableCell>
              <TableCell sx={{ maxWidth: 480 }}><Typography variant="caption">{d.reason}</Typography></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function JournalTab({ journal }) {
  if (!journal.length) {
    return <Typography variant="body2" color="text.secondary">Nothing has run yet.</Typography>;
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Started</TableCell>
            <TableCell>Run</TableCell>
            <TableCell>Session</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>What it did</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {journal.map((r) => (
            <TableRow key={r.id}>
              <TableCell>{formatIstDateTime(r.startedAt)}</TableCell>
              <TableCell>{r.runKind}</TableCell>
              <TableCell>{r.sessionDate}</TableCell>
              <TableCell><ToneChip label={r.status} /></TableCell>
              <TableCell><Typography variant="caption">{r.message}</Typography></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function RulesTab({ data }) {
  const p = data.parameters;
  const rows = [
    ['Signal', `A VCP base first seen FORMING: ${p.minContractions}+ contractions, each shallower than the last, Trend Template 6 of 7, detector score 0.65+.`],
    ['Stop quality', `Skipped when the stop sits under ${p.minStopAtr} ATR below the close — a stop inside half a day's range is a coin flip.`],
    ['Entry', `The next open, ₹${formatQty(num(p.positionRupees))} a position while the cash lasts. Not bought if it opens at or under its stop, and never a day late.`],
    ['Stop', "The final contraction's low, static, watched live through the session."],
    ['Trail', `After a close above the pivot within ${p.breakoutWindowSessions} sessions of the signal, out on the first close under the ${p.trailEmaSessions} EMA, sold at the next open.`],
    ['Cap', `${p.maxHoldSessions} sessions.`],
    ['No regime gate', 'An index 200-day filter was tested and made every variant worse; the Trend Template is already a per-stock regime test.'],
  ];
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Table size="small">
        <TableBody>
          {rows.map(([k, v]) => (
            <TableRow key={k}>
              <TableCell sx={{ fontWeight: 600, width: 160 }}>{k}</TableCell>
              <TableCell>{v}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
        Source: experiments/pattern-detection/FINAL_LOGIC.md. Numbers come from the strategy YAML
        and are editable from no page.
      </Typography>
    </Paper>
  );
}
