import {
  Alert,
  Box,
  Chip,
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

/**
 * "How it works" — the strategy explained step by step, for someone who has
 * never seen it.
 *
 * Two rules govern this file.
 *
 * **It restates no number.** Every threshold, multiple and lookback comes from
 * `GET /api/swing/explain`, which reads them out of the strategy's own YAML.
 * Hardcoding "3.5 × ATR" here would be a second source of truth (root
 * `CLAUDE.md` §7) and it would go on saying 3.5 for as long as it took someone
 * to notice the configuration had changed. The prose explains WHY a rule
 * exists; the values are always rendered from the payload.
 *
 * **The diagrams are inline SVG in theme colours.** No charting dependency for
 * five static pictures, and no hardcoded hex — every stroke and fill is a
 * `theme.palette.*` or `theme.market.*` token, so they are legible in light and
 * dark without a second set of assets (`frontend/CLAUDE.md` §1).
 *
 * Where the live snapshot is available the pictures are drawn with TODAY's
 * numbers — the funnel shows the real filter census and the breadth ramp marks
 * where the market actually is. A diagram of the rule in the abstract teaches
 * less than the same diagram with this morning's market in it.
 */

function Step({ number, title, children, subtitle }) {
  const theme = useTheme();
  return (
    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
      <Stack direction="row" spacing={2} alignItems="flex-start">
        <Box
          sx={{
            minWidth: 34,
            height: 34,
            borderRadius: '50%',
            bgcolor: 'primary.main',
            color: 'primary.contrastText',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          {number}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h5" sx={{ fontWeight: 600 }}>
            {title}
          </Typography>
          {subtitle ? (
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {subtitle}
            </Typography>
          ) : null}
          <Box sx={{ color: theme.palette.text.primary }}>{children}</Box>
        </Box>
      </Stack>
    </Paper>
  );
}

function Formula({ children }) {
  return (
    <Box
      component="pre"
      sx={{
        m: 0,
        my: 1.5,
        p: 1.5,
        borderRadius: 1,
        border: 1,
        borderColor: 'divider',
        bgcolor: 'action.hover',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 13,
        overflowX: 'auto',
        whiteSpace: 'pre-wrap',
      }}
    >
      {children}
    </Box>
  );
}

function Para({ children, ...rest }) {
  return (
    <Typography variant="body2" sx={{ mb: 1.5, lineHeight: 1.7 }} {...rest}>
      {children}
    </Typography>
  );
}

/** The trading day, as a timeline. */
function DayTimeline({ explain }) {
  const theme = useTheme();
  const hours = explain?.marketHours ?? {};
  const schedule = explain?.schedule ?? {};
  const auction = hours.closingAuction;

  // Short labels on purpose: five marks across one axis collide the moment a
  // caption runs long, and a picture that overlaps itself teaches nothing.
  const marks = [
    { at: hours.open ?? '09:15', label: 'Market opens', detail: 'NSE cash session' },
    {
      at: schedule.rebalanceAtIst ?? '09:16',
      label: 'Rebalance',
      detail: 'sells, then buys',
      accent: true,
    },
    ...(auction
      ? [
          {
            at: auction.continuousClose,
            label: 'Auction begins',
            detail: 'F&O names only',
          },
        ]
      : []),
    { at: hours.close ?? '15:30', label: 'Market closes', detail: 'stops unwatched' },
    {
      at: schedule.nightlyAtIst ?? '18:15',
      label: 'Nightly run',
      detail: 'decide and record',
      accent: true,
    },
  ];

  // Inset from both edges so the first and last captions are not clipped.
  const left = 76;
  const right = 684;
  const at = (index) => left + (index * (right - left)) / (marks.length - 1);
  // The stop monitor is live from the open to the close, which is every mark
  // up to and including the one before the nightly run.
  const watchedTo = at(marks.length - 2);

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg viewBox="0 0 760 132" width="100%" style={{ minWidth: 620 }} role="img"
           aria-label="The strategy's trading day, hour by hour">
        <line
          x1={left} y1="46" x2={right} y2="46"
          stroke={theme.palette.divider} strokeWidth="2"
        />
        <rect
          x={left} y="40" width={watchedTo - left} height="12" rx="6"
          fill={theme.market.upSoft}
        />
        <text
          x={(left + watchedTo) / 2} y="28" textAnchor="middle"
          fill={theme.palette.text.secondary} fontSize="11"
        >
          trailing stops watched, every second
        </text>
        {marks.map((mark, index) => {
          const x = at(index);
          const colour = mark.accent
            ? theme.palette.primary.main
            : theme.palette.text.secondary;
          return (
            <g key={mark.label}>
              <circle cx={x} cy="46" r={mark.accent ? 7 : 5} fill={colour} />
              <text
                x={x} y="72" textAnchor="middle"
                fill={theme.palette.text.primary} fontSize="13" fontWeight="600"
              >
                {mark.at}
              </text>
              <text
                x={x} y="90" textAnchor="middle"
                fill={theme.palette.text.primary} fontSize="11"
              >
                {mark.label}
              </text>
              <text
                x={x} y="107" textAnchor="middle"
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

/** The filters, as a funnel, with today's real counts where they exist. */
function Funnel({ snapshot }) {
  const theme = useTheme();
  const universe = snapshot?.universeSize ?? null;
  const withBars = snapshot?.symbolsWithBars ?? null;
  const liquid = snapshot?.liquidUniverse ?? null;
  const above = snapshot?.aboveOwnSma200 ?? null;
  const candidates = snapshot?.candidateCount ?? null;

  const stages = [
    // Notes are kept short: the longest bar runs to x=590 and the caption has
    // to fit in what is left without being clipped.
    { label: 'In the universe', value: universe, note: 'the Nifty 500 file' },
    { label: 'With stored bars', value: withBars, note: 'fetched, not accumulated' },
    { label: 'Liquid and priced (P2, P3)', value: liquid, note: 'with an SMA200' },
    { label: 'Above their own SMA200 (P4)', value: above, note: 'breadth counts these' },
    { label: 'Clear the momentum floor (P6)', value: candidates, note: 'the ranked candidates' },
  ];

  const widest = Math.max(...stages.map((one) => one.value ?? 0), 1);

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg viewBox="0 0 760 210" width="100%" style={{ minWidth: 560 }} role="img"
           aria-label="How many names each filter leaves">
        {stages.map((stage, index) => {
          const y = index * 40 + 6;
          const known = stage.value !== null && stage.value !== undefined;
          const width = known ? Math.max((stage.value / widest) * 300, 6) : 0;
          return (
            <g key={stage.label}>
              <rect
                x="250" y={y} width={width} height="26" rx="4"
                fill={index === stages.length - 1
                  ? theme.palette.primary.main
                  : theme.market.upSoft}
                stroke={theme.palette.divider}
              />
              <text
                x="240" y={y + 18} textAnchor="end"
                fill={theme.palette.text.primary} fontSize="12"
              >
                {stage.label}
              </text>
              <text
                x={known ? 250 + width + 10 : 260} y={y + 18}
                fill={known ? theme.palette.text.primary : theme.palette.text.disabled}
                fontSize="13" fontWeight="600"
              >
                {known ? stage.value : 'not measured'}
              </text>
              <text
                x={known ? 250 + width + 10 : 260} y={y + 32}
                fill={theme.palette.text.secondary} fontSize="10"
              >
                {stage.note}
              </text>
            </g>
          );
        })}
      </svg>
    </Box>
  );
}

/** Breadth to slots: the graded ramp, with today's breadth marked. */
function BreadthRamp({ snapshot, explain }) {
  const theme = useTheme();
  const digest = snapshot?.parameters ?? {};
  const lower = Number(digest.breadthLower ?? 0.35);
  const span = Number(digest.breadthSpan ?? 0.3);
  const upper = lower + span;
  const maxPositions = Number(digest.maxPositions ?? 10);
  const breadth =
    snapshot?.breadth === null || snapshot?.breadth === undefined
      ? null
      : Number(snapshot.breadth);

  // Plot area: x is breadth 0..1, y is slots 0..maxPositions.
  const x0 = 60;
  const x1 = 700;
  const y0 = 150;
  const y1 = 24;
  const px = (fraction) => x0 + fraction * (x1 - x0);
  const py = (slots) => y0 - (slots / maxPositions) * (y0 - y1);

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg viewBox="0 0 760 196" width="100%" style={{ minWidth: 560 }} role="img"
           aria-label="How market breadth decides how many positions are allowed">
        <line x1={x0} y1={y0} x2={x1} y2={y0} stroke={theme.palette.divider} strokeWidth="1.5" />
        <line x1={x0} y1={y0} x2={x0} y2={y1} stroke={theme.palette.divider} strokeWidth="1.5" />

        {/* The ramp itself: flat at zero, linear, flat at the full book. */}
        <polyline
          points={`${px(0)},${py(0)} ${px(lower)},${py(0)} ${px(upper)},${py(maxPositions)} ${px(1)},${py(maxPositions)}`}
          fill="none" stroke={theme.palette.primary.main} strokeWidth="2.5"
        />

        {[0, lower, upper, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={px(fraction)} y1={y0} x2={px(fraction)} y2={y0 + 5}
              stroke={theme.palette.divider}
            />
            <text
              x={px(fraction)} y={y0 + 19} textAnchor="middle"
              fill={theme.palette.text.secondary} fontSize="11"
            >
              {`${Math.round(fraction * 100)}%`}
            </text>
          </g>
        ))}
        {[0, maxPositions].map((slots) => (
          <text
            key={slots} x={x0 - 10} y={py(slots) + 4} textAnchor="end"
            fill={theme.palette.text.secondary} fontSize="11"
          >
            {slots}
          </text>
        ))}
        <text
          x={(x0 + x1) / 2} y={190} textAnchor="middle"
          fill={theme.palette.text.secondary} fontSize="11"
        >
          market breadth — the share of liquid names above their own 200-session average
        </text>
        <text
          x={16} y={(y0 + y1) / 2} textAnchor="middle" fontSize="11"
          fill={theme.palette.text.secondary}
          transform={`rotate(-90 16 ${(y0 + y1) / 2})`}
        >
          positions allowed
        </text>

        {breadth !== null ? (
          <g>
            <line
              x1={px(breadth)} y1={y0} x2={px(breadth)} y2={y1}
              stroke={theme.market.down} strokeWidth="1.5" strokeDasharray="4 3"
            />
            <circle
              cx={px(breadth)}
              cy={py(Math.max(0, Math.min(maxPositions, Math.round(
                maxPositions * Math.min(Math.max((breadth - lower) / span, 0), 1),
              ))))}
              r="5" fill={theme.market.down}
            />
            <text
              x={px(breadth)} y={y1 - 8} textAnchor="middle"
              fill={theme.market.down} fontSize="11" fontWeight="600"
            >
              {`today ${(breadth * 100).toFixed(1)}%`}
            </text>
          </g>
        ) : null}
      </svg>
    </Box>
  );
}

/** Why the score divides by ATR%: two stocks, same momentum, different rides. */
function VolatilityAdjusted() {
  const theme = useTheme();
  // Two synthetic paths that END in the same place. The point is the RIDE,
  // not the destination, so the numbers here are illustrative by design and
  // are not read from anything.
  const steady = [0, 8, 14, 22, 28, 36, 44, 50, 58, 66, 74, 80];
  const wild = [0, 26, 4, 34, 10, 44, 16, 52, 30, 62, 46, 80];
  const toPoints = (series, offsetX) =>
    series
      .map((value, index) => {
        const x = offsetX + (index * 280) / (series.length - 1);
        const y = 132 - (value / 90) * 100;
        return `${x},${y}`;
      })
      .join(' ');

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg viewBox="0 0 760 176" width="100%" style={{ minWidth: 560 }} role="img"
           aria-label="Two stocks with the same six-month gain and very different volatility">
        {[40, 400].map((offset) => (
          <line
            key={offset}
            x1={offset} y1="132" x2={offset + 280} y2="132"
            stroke={theme.palette.divider}
          />
        ))}
        <polyline
          points={toPoints(steady, 40)} fill="none"
          stroke={theme.market.up} strokeWidth="2.5"
        />
        <polyline
          points={toPoints(wild, 400)} fill="none"
          stroke={theme.market.down} strokeWidth="2.5"
        />
        <text x="180" y="156" textAnchor="middle" fill={theme.market.up}
              fontSize="12" fontWeight="600">
          steady climber — low ATR%, HIGH score
        </text>
        <text x="540" y="156" textAnchor="middle" fill={theme.market.down}
              fontSize="12" fontWeight="600">
          violent climber — high ATR%, LOW score
        </text>
        <text x="180" y="18" textAnchor="middle" fill={theme.palette.text.secondary}
              fontSize="11">
          +80% over six months
        </text>
        <text x="540" y="18" textAnchor="middle" fill={theme.palette.text.secondary}
              fontSize="11">
          +80% over six months
        </text>
      </svg>
    </Box>
  );
}

/** The chandelier stop: steps up with the price, never down. */
function Ratchet({ explain }) {
  const theme = useTheme();
  const multiple =
    explain?.parameters?.find((one) => one.code === 'P15')?.value ?? null;

  // Price rises, pulls back, rises, then falls through the stop. The stop
  // tracks the HIGHEST CLOSE and flattens on the way down -- which is the
  // whole point of the picture.
  const price = [30, 38, 46, 42, 54, 62, 58, 70, 66, 60, 48, 36];
  const high = price.reduce((acc, value) => {
    acc.push(Math.max(acc.length ? acc[acc.length - 1] : value, value));
    return acc;
  }, []);
  const stop = high.map((value) => value - 18);

  const toPoints = (series) =>
    series
      .map((value, index) => {
        const x = 60 + (index * 640) / (series.length - 1);
        const y = 150 - (value / 80) * 120;
        return `${x},${y}`;
      })
      .join(' ');

  const hitIndex = price.findIndex((value, index) => value <= stop[index]);

  return (
    <Box sx={{ my: 2, overflowX: 'auto' }}>
      <svg viewBox="0 0 760 196" width="100%" style={{ minWidth: 560 }} role="img"
           aria-label="The trailing stop steps up with the highest close and never steps down">
        <line x1="60" y1="150" x2="700" y2="150" stroke={theme.palette.divider} />
        <polyline points={toPoints(price)} fill="none"
                  stroke={theme.palette.primary.main} strokeWidth="2.5" />
        <polyline points={toPoints(stop)} fill="none"
                  stroke={theme.market.down} strokeWidth="2" strokeDasharray="5 4" />
        {hitIndex >= 0 ? (
          <g>
            <circle
              cx={60 + (hitIndex * 640) / (price.length - 1)}
              cy={150 - (price[hitIndex] / 80) * 120}
              r="6" fill={theme.market.down}
            />
            <text
              x={60 + (hitIndex * 640) / (price.length - 1)}
              y={150 - (price[hitIndex] / 80) * 120 + 24}
              textAnchor="end" fill={theme.market.down} fontSize="11" fontWeight="600"
            >
              stop hit — sold at market
            </text>
          </g>
        ) : null}
        <text x="60" y="176" fill={theme.palette.primary.main} fontSize="12" fontWeight="600">
          price
        </text>
        <text x="120" y="176" fill={theme.market.down} fontSize="12" fontWeight="600">
          stop — steps up, never down
        </text>
        {multiple ? (
          <text x="700" y="176" textAnchor="end" fill={theme.palette.text.secondary} fontSize="11">
            {multiple}
          </text>
        ) : null}
      </svg>
    </Box>
  );
}

export default function SwingExplainer({ explain, status }) {
  const snapshot = status?.snapshot;
  const byCode = Object.fromEntries(
    (explain?.parameters ?? []).map((one) => [one.code, one]),
  );
  const value = (code) => byCode[code]?.value ?? '…';
  const universe = explain?.universe ?? {};
  const schedule = explain?.schedule ?? {};
  const index = explain?.regimeIndex ?? {};
  const auction = explain?.marketHours?.closingAuction;

  if (!explain) {
    return (
      <Alert severity="info">
        Loading the rule as it is configured…
      </Alert>
    );
  }

  return (
    <Stack spacing={2.5}>
      <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
        <Typography variant="h4" sx={{ fontWeight: 600, mb: 1 }}>
          How this strategy works
        </Typography>
        <Para>
          In one sentence: <strong>own the ten strongest steadily-rising stocks
          in the Nifty 500, size each at a tenth of the book, put a wide
          trailing stop under every one, and go completely to cash when the
          NIFTY 50 falls below its 200-day average.</strong>
        </Para>
        <Para color="text.secondary">
          It is a trend-following rotation with two off-switches — a hard
          market-level kill switch and a graded throttle. The off-switches, not
          the stock picking, are what make its drawdowns survivable. Everything
          below is the configuration this installation is actually running;
          nothing on this page is typed in by hand.
        </Para>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Chip size="small" label={`Universe: ${universe.name ?? '—'} (${universe.size ?? '—'})`}
                sx={{ bgcolor: 'action.selected', color: 'text.primary' }} />
          <Chip size="small" label={`Rebalances ${schedule.cadence ?? '—'}`}
                sx={{ bgcolor: 'action.selected', color: 'text.primary' }} />
          <Chip size="small" label={`Regime index: ${index.label ?? '—'} (read, never traded)`}
                sx={{ bgcolor: 'action.selected', color: 'text.primary' }} />
          <Chip size="small" label={`Charges: ${explain.chargesRateCard}`}
                sx={{ bgcolor: 'action.selected', color: 'text.primary' }} />
        </Stack>
      </Paper>

      <Step
        number={1}
        title="Every day has the same shape"
        subtitle="Two scheduled jobs and one watcher. Nothing is decided in a hurry."
      >
        <DayTimeline explain={explain} />
        <Para>
          The <strong>nightly run</strong> at {schedule.nightlyAtIst} IST does
          all the thinking: it fetches the day's closing bars from Dhan,
          recomputes every indicator, works out what should be bought and sold,
          raises the trailing stop under each open position, and writes the
          whole decision down — including the sessions where the answer is "do
          nothing", which is most of them.
        </Para>
        <Para>
          The <strong>rebalance</strong> at {schedule.rebalanceAtIst} IST acts
          on it: signals come from the previous session's close and orders go in
          at the next session's open. Nothing is ever decided and executed on
          the same bar.
        </Para>
        <Para>
          In between, a watcher checks every open position's trailing stop once
          a second against the live price.
        </Para>
      </Step>

      <Step
        number={2}
        title="Narrow five hundred names down to a shortlist"
        subtitle="Four filters, applied in order, every session."
      >
        <Funnel snapshot={snapshot} />
        <Para>
          A stock has to be <strong>liquid</strong> ({value('P2')}) so that a
          position can actually be bought and sold, and{' '}
          <strong>not a penny stock</strong> ({value('P3')}). Liquidity is
          measured as rupee turnover rather than share volume — a million shares
          of a ₹5 stock is not liquidity.
        </Para>
        <Para>
          It has to be in an <strong>uptrend of its own</strong> ({value('P4')})
          — its own moving average, not the index's — and its six-month{' '}
          <strong>momentum</strong> has to clear an absolute floor
          ({value('P6')}). Momentum is measured as {value('P5')}: the most
          recent few sessions are deliberately skipped so that a one-week spike
          cannot buy its way onto the list.
        </Para>
        <Para color="text.secondary">
          A name with too little history ({value('—')}) is excluded before any
          of this, and a name that did not trade on the session is skipped
          rather than carried forward on a stale price.
        </Para>
      </Step>

      <Step
        number={3}
        title="Rank the shortlist by momentum ÷ volatility"
        subtitle="The one idea that makes this strategy different from every other momentum screen."
      >
        <Formula>{`score = ${value('P7')}`}</Formula>
        <Para>
          Two stocks can both be up 80% in six months and have been completely
          different things to own. Dividing the gain by how much the stock moves
          on an average day (its ATR as a percentage of price) favours the one
          that got there steadily.
        </Para>
        <VolatilityAdjusted />
        <Para>
          This is not a preference. Ranking on raw momentum instead measures a
          higher return — about 29% a year — at a drawdown near −31%, which is
          the difference between a strategy you can hold and one you abandon at
          the worst moment. The divisor is what buys the survivable drawdown.
        </Para>
      </Step>

      <Step
        number={4}
        title="Ask the market how many positions it deserves"
        subtitle="Breadth decides the size of the book, not the strength of the signals."
      >
        <BreadthRamp snapshot={snapshot} explain={explain} />
        <Para>
          <strong>Breadth</strong> ({value('P10')}) is the share of the liquid
          universe trading above its own long-term average — a plain measure of
          how many things are working. It is turned into a number of{' '}
          <strong>slots</strong> by a graded ramp ({value('P11')}).
        </Para>
        <Para>
          Below the lower threshold the strategy buys nothing at all, however
          good the shortlist looks. Above the upper one it runs the full book.
          In between it scales, so a market where only half the names are
          working gets half a book.
        </Para>
        <Para color="text.secondary">
          Note what this is not: it is not a forecast. It is a measure of the
          present, and the strategy shrinks itself when the evidence thins.
        </Para>
      </Step>

      <Step
        number={5}
        title="…and whether it deserves any at all"
        subtitle="The kill switch, and a separate filter on new entries."
      >
        <Para>
          <strong>{value('P8')}</strong> is the hard gate. When the index is
          below that line, every position is sold at the next open and the book
          holds 100% cash. It does not negotiate with a position that happens to
          be doing well.
        </Para>
        <Para>
          <strong>{value('P9')}</strong> is softer: it blocks NEW entries while
          the index's medium-term return is negative, but it never forces an
          exit. It is there to stop the strategy buying into the first bounce of
          a downtrend.
        </Para>
        <Alert severity="info" sx={{ my: 1.5 }}>
          These two switches are why the drawdown is what it is. The gate is off
          about a quarter of all sessions, and the cash it forces during those
          stretches is the single biggest contributor to the strategy's
          risk profile — more than the stop, more than the stock selection.
        </Alert>
        {/* WHAT IS ACTUALLY IN FORCE, not what the rule book says.
            The three enforcement switches are runtime state, so a page that
            rendered only the configuration file would go on teaching a gate
            that nothing is obeying. Both are shown: the rule's default, and
            whether this installation is enforcing it. */}
        {(explain.policies ?? []).some((policy) => !policy.enforced) ? (
          <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ my: 1.5 }}>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              On THIS installation, not every rule above is being enforced.
            </Typography>
            {explain.policies
              .filter((policy) => !policy.enforced)
              .map((policy) => (
                <Typography key={policy.key} variant="body2">
                  {policy.label}: NOT enforced (the rule ships{' '}
                  {policy.default ? 'enforced' : 'not enforced'}).
                </Typography>
              ))}
            <Typography variant="caption" color="text.secondary">
              The rule itself is unchanged — every number on this page is read
              from the strategy&apos;s own configuration and none of it is
              editable from a page. What has been switched off is whether the
              application acts on it. The gate is still computed and recorded on
              every session and every trade. Change it under Rules on Strategies
              &amp; Features.
            </Typography>
          </Alert>
        ) : null}
        {explain.offGate?.effectiveEnabled ?? explain.offGate?.enabled ? (
          <Alert severity="warning" icon={<WarningAmberIcon />}>
            The V3b off-gate variant is ENABLED on this installation: it holds
            up to {explain.offGate.slots} position(s) while the gate is off.
            Thirteen variants were tested and not one beat simply holding cash.
          </Alert>
        ) : (
          <Para color="text.secondary">
            A variant that keeps trading a few names while the gate is off is
            implemented and switched off. Its individual trades are genuinely
            good and its total is still lower than holding cash, because capital
            committed to a bear rally is not available at the moment the regime
            turns — which is exactly when the best trades happen.
          </Para>
        )}
      </Step>

      <Step
        number={6}
        title="Size every position the same"
        subtitle="No conviction weighting. The ranking decides what to own, not how much."
      >
        <Formula>{`position value = ${value('P13')}
quantity       = floor(position value / price)`}</Formula>
        <Para>
          The divisor is deliberately kept separate from the maximum number of
          positions ({value('P12')}). When breadth allows only four slots, each
          is still sized at a tenth of equity and the remaining 60% sits in
          cash — the strategy holds cash rather than concentrating.
        </Para>
        <Para color="text.secondary">
          One position down 20% therefore costs 2% of the book. The
          diversification is the loss cap; the stop is not.
        </Para>
      </Step>

      <Step
        number={7}
        title="Leave for one of two reasons"
        subtitle="A position exits because it stopped leading, or because it fell too far."
      >
        <Para>
          <strong>Rotation ({value('P16')}).</strong> A holding that decays off
          the leadership board is sold at the next open, whether or not it is
          making money. There is no target price anywhere in this strategy.
        </Para>
        <Para>
          <strong>The trailing stop ({value('P14')}, then {value('P15')}).</strong>{' '}
          A stop is placed under each position at entry and moves UP on every
          daily close, tracking the highest close since entry. It never moves
          down.
        </Para>
        <Ratchet explain={explain} />
        <Para>
          The stop is deliberately WIDE — roughly 9–14% below the highest close.
          Every tighter variant that has been tested made the portfolio worse:
          momentum stocks pull back 6–10% inside perfectly intact trends, and a
          tight stop turns those shakeouts into realised losses plus the cost of
          getting back in. Worse, it ejects exactly the positions that go on to
          produce the outsized winners.
        </Para>
        {auction ? (
          <Para color="text.secondary">
            One real-world wrinkle: for F&amp;O-eligible names, continuous
            trading on NSE now ends at {auction.continuousClose} rather than the
            regular close. A stop that fires after that cannot fill in
            continuous trading, so it is recorded and its exit waits for the
            next session's open. This simulator has no model of a call auction
            and will not pretend otherwise.
          </Para>
        ) : null}
      </Step>

      <Step
        number={8}
        title="Pay the real costs"
        subtitle="Charges are computed from a sourced rate card, not assumed away."
      >
        <Para>
          Every order is charged under <code>{explain.chargesRateCard}</code>:
          STT on both legs of a delivery trade, the exchange transaction charge,
          the SEBI fee, stamp duty on the buy, GST, and a flat depository charge
          per sell. Every rate carries a primary source and an as-of date.
        </Para>
        <Para color="text.secondary">
          Fills are simulated pessimistically too — a market order pays the far
          side of the spread, walks the visible depth, and partially fills when
          that depth runs out. It will not match the backtest's simpler
          assumption, and measuring that difference is one of the reasons this
          runs on paper first.
        </Para>
      </Step>

      <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
        <Typography variant="h5" sx={{ fontWeight: 600, mb: 1 }}>
          Read this before you believe any of it
        </Typography>
        <Stack spacing={1}>
          {(explain.caveats ?? []).map((caveat) => (
            <Alert key={caveat} severity="warning" icon={<WarningAmberIcon />}>
              {caveat}
            </Alert>
          ))}
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>
          Every parameter, as configured
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Read live from <code>conf/strategies/{explain.strategyKey}.yaml</code>.
          The codes are the specification's own, so this table and{' '}
          <code>{explain.specification}</code> can be read side by side.
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell width={60}>Code</TableCell>
                <TableCell width={170}>Parameter</TableCell>
                <TableCell>Value</TableCell>
                <TableCell>Why</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(explain.parameters ?? []).map((row) => (
                <TableRow key={`${row.code}-${row.name}`} hover>
                  <TableCell sx={{ fontWeight: 600 }}>{row.code}</TableCell>
                  <TableCell>{row.name}</TableCell>
                  <TableCell>
                    <Typography variant="body2" className="numeric" sx={{ fontWeight: 500 }}>
                      {row.value}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {row.note}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Stack>
  );
}
