import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Divider,
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
import { useTheme } from '@mui/material/styles';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { btstApi } from '../api/btst';

/**
 * THE DAY, as a timeline — the single most explanatory picture in this module.
 *
 * Every mark is a configured time read off the payload: the session, the
 * moment the universe joins the feed, the moment continuous trading ends for
 * F&O names, the scan, and the next morning's exit. Two of them are on the
 * wrong side of each other on purpose (the scan is INSIDE the auction window),
 * which is exactly the thing a reader should see rather than be told.
 */
function DayTimeline({ explain }) {
  const theme = useTheme();
  const hours = explain?.marketHours || {};
  const schedule = explain?.schedule || {};
  const subscription = explain?.subscription || {};
  const auction = hours.closingAuction;

  const marks = [
    { at: hours.open || '09:15', label: 'Market opens', detail: 'NSE cash session' },
    ...(subscription.windowOpensAtIst
      ? [
          {
            at: subscription.windowOpensAtIst,
            label: 'Universe joins',
            detail: '~289 on the feed',
          },
        ]
      : []),
    ...(auction
      ? [
          {
            at: auction.continuousClose,
            label: 'Auction begins',
            detail: 'F&O names only',
            warn: true,
          },
        ]
      : []),
    {
      at: schedule.scanAtIst || '15:20',
      label: 'Scan and BUY',
      detail: 'one pass, live prices',
      accent: true,
    },
    { at: hours.close || '15:30', label: 'Market closes', detail: 'held overnight' },
    {
      at: schedule.exitAtIst || '09:16',
      label: 'SELL everything',
      detail: 'next session, no condition',
      accent: true,
    },
  ];

  const left = 74;
  const right = 686;
  const at = (index) => left + (index * (right - left)) / (marks.length - 1);
  // The overnight hold: from the buy to the sell, which is the last two marks
  // but one — everything after the scan is the position being carried.
  const scanIndex = marks.findIndex((one) => one.label === 'Scan and BUY');

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg
        viewBox="0 0 760 150"
        width="100%"
        style={{ minWidth: 640 }}
        role="img"
        aria-label="The strategy's day: buy near the close, sell at the next open"
      >
        <line
          x1={left} y1="52" x2={right} y2="52"
          stroke={theme.palette.divider} strokeWidth="2"
        />
        {/* THE ONLY RISK WINDOW, and the one in which no order can execute at
            any price. That is why there is no stop. */}
        <rect
          x={at(scanIndex)} y="46" width={at(marks.length - 1) - at(scanIndex)} height="12"
          rx="6" fill={theme.market.downSoft}
        />
        <text
          x={(at(scanIndex) + at(marks.length - 1)) / 2} y="34" textAnchor="middle"
          fill={theme.palette.text.secondary} fontSize="11"
        >
          held overnight — no stop is possible here
        </text>
        {marks.map((mark, index) => {
          const x = at(index);
          const colour = mark.accent
            ? theme.palette.primary.main
            : mark.warn
              ? theme.market.down
              : theme.palette.text.secondary;
          return (
            <g key={`${mark.at}-${mark.label}`}>
              <circle cx={x} cy="52" r={mark.accent ? 7 : 5} fill={colour} />
              <text
                x={x} y="80" textAnchor="middle"
                fill={theme.palette.text.primary} fontSize="13" fontWeight="600"
              >
                {mark.at}
              </text>
              <text
                x={x} y="98" textAnchor="middle"
                fill={theme.palette.text.primary} fontSize="11"
              >
                {mark.label}
              </text>
              <text
                x={x} y="115" textAnchor="middle"
                fill={theme.palette.text.secondary} fontSize="10"
              >
                {mark.detail}
              </text>
            </g>
          );
        })}
      </svg>
    </Box>
  );
}

/** Where the threshold's caption sits, clear of the two dot captions. */
const THRESHOLD_X = 560;

/**
 * CLV — the least intuitive rule in the strategy, settled by a picture.
 *
 * `(price − low) / (high − low)` measured on the session SO FAR, and the
 * shaded band is where the price has to be. The threshold is read from the
 * payload, so a configuration change moves the shading rather than leaving the
 * caption saying 0.8 for as long as it takes somebody to notice.
 */
function CloseLocation({ explain }) {
  const theme = useTheme();
  const floor = explain?.thresholds?.closeLocationMinimum;
  if (floor === null || floor === undefined) return null;

  // The candle's geometry in the picture. `top` is the session high and
  // `bottom` the session low; the qualifying band is the top (1 − floor) of it.
  const top = 26;
  const bottom = 150;
  const span = bottom - top;
  const bandBottom = top + span * (1 - floor);
  const qualifying = top + span * 0.12;
  const rejected = top + span * 0.72;

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg
        viewBox="0 0 760 186"
        width="100%"
        style={{ minWidth: 520 }}
        role="img"
        aria-label="Where in the day's range the price has to be"
      >
        {/* The qualifying band. */}
        <rect
          x="150" y={top} width="300" height={bandBottom - top}
          fill={theme.market.upSoft}
        />
        <line
          x1="150" y1={bandBottom} x2="450" y2={bandBottom}
          stroke={theme.market.up} strokeWidth="1.5" strokeDasharray="5 4"
        />
        {/* The day's range. */}
        <line x1="300" y1={top} x2="300" y2={bottom} stroke={theme.palette.divider} strokeWidth="2" />
        <line x1="270" y1={top} x2="330" y2={top} stroke={theme.palette.text.secondary} strokeWidth="2" />
        <line x1="270" y1={bottom} x2="330" y2={bottom} stroke={theme.palette.text.secondary} strokeWidth="2" />

        <text x="140" y={top + 5} textAnchor="end" fill={theme.palette.text.primary} fontSize="12">
          session high so far
        </text>
        <text x="140" y={bottom + 5} textAnchor="end" fill={theme.palette.text.primary} fontSize="12">
          session low so far
        </text>
        {/* The threshold's own label sits in its own column, clear of the two
            dot captions: at x=462 the longest of those ran into it. */}
        <text
          x={THRESHOLD_X} y={bandBottom + 4}
          fill={theme.market.up} fontSize="12" fontWeight="600"
        >
          CLV = {floor}
        </text>
        <text
          x={THRESHOLD_X} y={bandBottom + 20}
          fill={theme.palette.text.secondary} fontSize="10"
        >
          the top {Math.round((1 - floor) * 100)}% of the range
        </text>

        {/* A price that qualifies, and one that does not. */}
        <circle cx="300" cy={qualifying} r="6" fill={theme.market.up} />
        <text x="318" y={qualifying + 4} fill={theme.market.up} fontSize="11" fontWeight="600">
          qualifies — closing on its highs
        </text>
        <circle cx="300" cy={rejected} r="6" fill={theme.market.down} />
        <text x="318" y={rejected + 4} fill={theme.market.down} fontSize="11" fontWeight="600">
          rejected — gave the day back
        </text>

        <text x="150" y={bottom + 28} fill={theme.palette.text.secondary} fontSize="10">
          A zero-range session has no CLV at all — it is undefined, not 1.0, and buys nothing.
        </text>
      </svg>
    </Box>
  );
}

/**
 * THE EXIT-TIMING CLIFF, drawn rather than asserted.
 *
 * The prose states +0.617% at 71.4% against +0.428% at 49.0%; side by side the
 * two bars make the case the sentence only claims. Both figures come from the
 * payload's `exitTiming`.
 */
function ExitTimingCliff({ exitTiming }) {
  const theme = useTheme();
  const openGap = exitTiming?.nextOpenMeanGap;
  const closeGap = exitTiming?.nextCloseMeanGap;
  if (openGap === null || openGap === undefined) return null;
  if (closeGap === null || closeGap === undefined) return null;

  const widest = Math.max(openGap, closeGap, 0.0001);
  const scale = 240;
  const bars = [
    {
      label: 'Sold at the next OPEN',
      detail: 'what this strategy does',
      gap: openGap,
      rate: exitTiming.nextOpenWinRate,
      colour: theme.market.up,
    },
    {
      label: 'Held to the next CLOSE',
      detail: 'the identical signals',
      gap: closeGap,
      rate: exitTiming.nextCloseWinRate,
      colour: theme.market.down,
    },
  ];

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg
        viewBox="0 0 760 150"
        width="100%"
        style={{ minWidth: 560 }}
        role="img"
        aria-label="The same signals, two exit timings"
      >
        {bars.map((bar, index) => {
          const y = index * 64 + 14;
          const width = Math.max((bar.gap / widest) * scale, 4);
          const rate = bar.rate;
          const rateWidth = rate ? Math.max(rate * scale, 4) : 0;
          return (
            <g key={bar.label}>
              <text x="240" y={y + 16} textAnchor="end" fill={theme.palette.text.primary} fontSize="12">
                {bar.label}
              </text>
              <text x="240" y={y + 32} textAnchor="end" fill={theme.palette.text.secondary} fontSize="10">
                {bar.detail}
              </text>
              <rect x="252" y={y} width={width} height="20" rx="3" fill={bar.colour} />
              <text
                x={252 + width + 8} y={y + 15}
                fill={theme.palette.text.primary} fontSize="13" fontWeight="600"
              >
                {`+${(bar.gap * 100).toFixed(3)}%`}
              </text>
              <rect
                x="252" y={y + 24} width={rateWidth} height="10" rx="3"
                fill={bar.colour} opacity="0.35"
              />
              <text
                x={252 + rateWidth + 8} y={y + 33}
                fill={theme.palette.text.secondary} fontSize="10"
              >
                {rate ? `${(rate * 100).toFixed(1)}% win rate` : 'win rate not measured'}
              </text>
            </g>
          );
        })}
        <text x="252" y="142" fill={theme.palette.text.secondary} fontSize="10">
          Same entries, same universe, same dates. Only the moment of sale differs.
        </text>
      </svg>
    </Box>
  );
}

/**
 * THE FILTER FUNNEL, with the thresholds labelled — and with the last recorded
 * scan's real census where one exists.
 *
 * `BtstFunnel` draws the counts as bars on the Signals tab; this draws the
 * SHAPE with what each stage is testing beside it, which is what a reader on a
 * page called "How it works" came for. Where the payload carries a scan it is
 * labelled with that scan's date rather than implied to be live — the live one
 * is the Signals tab, and a picture claiming to be today's when it is
 * yesterday's would be worse than one claiming nothing.
 */
/** The funnel's fixed columns: label | bar | count | what it tests. */
const BAR_X = 186;
const BAR_MAX = 190;
const COUNT_X = BAR_X + BAR_MAX + 60;
const TEST_X = COUNT_X + 14;

function FilterFunnel({ explain, lastScan }) {
  const theme = useTheme();
  const counts = new Map(
    (lastScan?.funnel || []).map((one) => [one.key, one.count]),
  );

  // The stages, their labels AND what each tests all come from the payload,
  // which builds them from the scan's own `FILTER_STAGES`. A list retyped here
  // would drift from the funnel the scan actually records — and did, in the
  // first draft of this diagram, where five of nine keys matched nothing and
  // every bar silently read "not measured".
  const stages = explain?.funnelStages || [];
  if (!stages.length) return null;

  const known = stages
    .map((one) => counts.get(one.key))
    .filter((one) => one !== null && one !== undefined);
  const widest = known.length ? Math.max(...known, 1) : null;

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg
        viewBox={`0 0 760 ${stages.length * 34 + 24}`}
        width="100%"
        style={{ minWidth: 620 }}
        role="img"
        aria-label="How many names each filter leaves, and what each one tests"
      >
        {stages.map((stage, index) => {
          const y = index * 34 + 8;
          const count = counts.get(stage.key);
          const measured = count !== null && count !== undefined;
          // The funnel's SHAPE when nothing has been measured: a tapering set
          // of bars that says "each of these removes names" without inventing
          // a number for how many.
          const width = measured
            ? Math.max((count / widest) * BAR_MAX, 5)
            : BAR_MAX - index * 18;
          return (
            <g key={stage.key}>
              <text
                x={BAR_X - 10} y={y + 15} textAnchor="end"
                fill={theme.palette.text.primary} fontSize="12"
              >
                {stage.label}
              </text>
              <rect
                x={BAR_X} y={y} width={width} height="22" rx="3"
                fill={
                  index === stages.length - 1
                    ? theme.palette.primary.main
                    : theme.market.upSoft
                }
                stroke={theme.palette.divider}
                opacity={measured ? 1 : 0.45}
              />
              {/* The count and the caption sit in FIXED columns rather than
                  after the bar. Trailing a variable-width bar put the longest
                  caption off the right-hand edge of the viewBox, where SVG
                  clips it silently — a diagram that cannot wrap has to be laid
                  out so it never needs to. */}
              <text
                x={COUNT_X} y={y + 15} textAnchor="end"
                fill={measured ? theme.palette.text.primary : theme.palette.text.disabled}
                fontSize="12" fontWeight="600"
              >
                {measured ? count : 'not measured'}
              </text>
              <text
                x={TEST_X} y={y + 15}
                fill={theme.palette.text.secondary} fontSize="10"
              >
                {stage.test}
              </text>
            </g>
          );
        })}
      </svg>
      <Typography variant="caption" color="text.secondary">
        {lastScan?.sessionDate
          ? `Counts from the scan recorded on ${lastScan.sessionDate}. Today's, as it forms, is on the Signals tab.`
          : 'No scan has been recorded yet, so the bars show the shape of the funnel and not a census. The Signals tab computes today’s live.'}
      </Typography>
    </Box>
  );
}

/**
 * How it works — and what is wrong with it.
 *
 * **THIS COMPONENT RESTATES NO NUMBER.** Every threshold, lookback and time
 * comes from `GET /api/btst/explain`, which reads them out of the strategy's
 * own YAML. Hardcoding "55-day high" in JSX would be a second source of truth
 * (root `CLAUDE.md` section 7) and it would go on saying 55 for as long as it
 * took somebody to notice the configuration had changed. The prose explains
 * WHY a rule exists; the values always come from the payload.
 *
 * It carries two things the handoff insisted on and that a page about a
 * strategy would normally leave out: the specification's own recommendation
 * NOT to fund this yet, and the year-by-year decay table. Both are above the
 * rules rather than below them, because they are the most important facts
 * about this strategy and a reader who stops half way should have seen them.
 *
 * **Its diagrams are inline SVG in theme tokens**, not a charting dependency
 * and not a hardcoded hex — four static pictures do not justify a library, and
 * `theme.palette.*` / `theme.market.*` is what makes them legible in both
 * modes without a second set of assets. Where a recorded scan exists the
 * funnel is drawn with ITS census, labelled with that scan's date: a picture
 * claiming to be today's when it is yesterday's would be worse than one
 * claiming nothing.
 */
export default function BtstExplainer({ strategyKey, status }) {
  const theme = useTheme();
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    btstApi
      .explain(strategyKey)
      .then((next) => {
        if (!cancelled) setPayload(next);
      })
      .catch((caught) => {
        if (!cancelled) setError(caught.message || 'Could not read the explainer');
      });
    return () => {
      cancelled = true;
    };
  }, [strategyKey]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!payload) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  const exitTiming = payload.exitTiming || {};
  const decay = payload.decay || {};
  // TODAY'S NUMBERS where there are any. The last recorded scan's funnel is
  // the real census the diagram is drawn with; with nothing ever scanned the
  // picture falls back to the funnel's SHAPE and says so, rather than drawing
  // a row of zeros that would read as a market in which nothing qualified.
  const lastScan = status?.lastScan || null;

  return (
    <Stack spacing={2}>
      <Alert severity="warning" icon={<WarningAmberIcon />}>
        {payload.recommendation}
      </Alert>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          What it does
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {payload.description}
        </Typography>

        {/* The whole strategy in one picture: buy near the close, carry it
            overnight with no stop, sell at the next open. Every mark is a
            configured time read off the payload. */}
        <DayTimeline explain={payload} />
        <Typography variant="caption" color="text.secondary">
          The scan and the buy are ONE pass, which is the opposite of the
          rotation's arrangement. It decides on the session so far — a running
          high, a running low and a cumulative volume — and none of that
          survives ten minutes, so a decision that cannot be carried forward has
          to be acted on where it is taken.
        </Typography>
      </Paper>

      {/* THE EXIT IS THE STRATEGY, so it gets its own panel above the rules. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          The exit is the strategy
        </Typography>
        <ExitTimingCliff exitTiming={exitTiming} />
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={4}>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Sold at the next OPEN — what this does
            </Typography>
            <Typography variant="h6" className="numeric" sx={{ color: theme.market.up }}>
              {`+${(exitTiming.nextOpenMeanGap * 100).toFixed(3)}%`}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {(exitTiming.nextOpenWinRate * 100).toFixed(1)}% win rate
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Held to the next CLOSE instead — same signals
            </Typography>
            <Typography variant="h6" className="numeric" sx={{ color: theme.market.down }}>
              {`+${(exitTiming.nextCloseMeanGap * 100).toFixed(3)}%`}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {(exitTiming.nextCloseWinRate * 100).toFixed(1)}% win rate
            </Typography>
          </Box>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
          {exitTiming.note}
        </Typography>
      </Paper>

      {/* The decay table, which the handoff named explicitly. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          Year by year — the decay
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Year</TableCell>
                <TableCell align="right">Mean per trade</TableCell>
                <TableCell align="right">Win rate</TableCell>
                <TableCell align="right">Trades</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {(decay.rows || []).map((one) => (
                <TableRow key={one.year}>
                  <TableCell>{one.year}</TableCell>
                  <TableCell
                    align="right"
                    className="numeric"
                    sx={{
                      color: one.meanGap >= 0 ? theme.market.up : theme.market.down,
                    }}
                  >
                    {`${one.meanGap >= 0 ? '+' : ''}${(one.meanGap * 100).toFixed(3)}%`}
                  </TableCell>
                  <TableCell align="right" className="numeric">
                    {(one.winRate * 100).toFixed(1)}%
                  </TableCell>
                  <TableCell align="right" className="numeric">
                    {one.trades}
                  </TableCell>
                  <TableCell>
                    {one.year === 2021 ? (
                      <Chip size="small" color="warning" label="+101.3% of the whole result" />
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <Alert severity="warning" sx={{ mt: 1.5 }}>
          {decay.note}
        </Alert>
      </Paper>

      {/* THE FUNNEL, with what each stage tests — and with the last recorded
          scan's real census where there is one. A diagram of the rule in the
          abstract teaches less than the same diagram with a real market in it. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          The filters, and how far the universe gets
        </Typography>
        <FilterFunnel explain={payload} lastScan={lastScan} />
        <Typography variant="caption" color="text.secondary">
          “Nothing qualified” is the ORDINARY outcome — about one signal every
          two sessions with F&amp;O names excluded. Every session records the
          whole funnel, which is what makes a working scan that found nothing
          distinguishable from one that did not run.
        </Typography>
      </Paper>

      {/* B6 is the least intuitive rule here, and a picture settles it. */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          Where it has to close — B6
        </Typography>
        <CloseLocation explain={payload} />
        <Typography variant="caption" color="text.secondary">
          Measured on the session SO FAR, against the day's running high and
          low, not against a finished bar. Those two figures are read straight
          off the feed's own aggregate fields — and whether they are mapped the
          way this assumes is the one thing about this strategy that has never
          been checked against a live session. If they were transposed this
          filter would not drift, it would INVERT.
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          The rules, as configured
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Every value here is read from the strategy's own YAML. Nothing on this
          page is typed in twice.
        </Typography>
        <TableContainer sx={{ mt: 1 }}>
          <Table size="small">
            <TableBody>
              {(payload.parameters || []).map((one) => (
                <TableRow key={one.code}>
                  <TableCell sx={{ width: 60 }}>
                    <Chip size="small" label={one.code} />
                  </TableCell>
                  <TableCell sx={{ width: 180 }}>{one.name}</TableCell>
                  <TableCell>{one.value}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        {payload.regimeIndex ? (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            The regime index is {payload.regimeIndex.label} (
            {payload.regimeIndex.segment}), which this strategy READS and never
            trades.
          </Typography>
        ) : null}
        {/* B10 is the one place the specification contradicts itself, and the
            page says so rather than presenting a choice as a given. */}
        <Divider sx={{ my: 1.5 }} />
        <Typography variant="caption" color="text.secondary">
          <strong>On B10, the ranking.</strong> The specification contradicts
          itself: B10's own table says rank by volume ratio, and its section 16
          recommends six-month momentum, which measured the better risk-adjusted
          return (MAR 2.12 against 2.05). B10 ships, because section 9.3 also
          shows ranking is worth at most 0.9 points of CAGR over ranking
          alphabetically and binds on only 5.7% of signal-days — so deviating
          buys almost nothing and costs comparability with every other number
          in the document.
        </Typography>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 1 }}>
          What is NOT verified
        </Typography>
        <Stack spacing={1}>
          {(payload.caveats || []).map((one, index) => (
            <Typography key={index} variant="body2" color="text.secondary">
              • {one}
            </Typography>
          ))}
        </Stack>
      </Paper>
    </Stack>
  );
}
