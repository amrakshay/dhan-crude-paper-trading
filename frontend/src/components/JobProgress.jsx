import { useEffect, useState } from 'react';
import { Box, LinearProgress, Stack, Tooltip, Typography } from '@mui/material';

/**
 * How far through a long scheduled job the strategy is.
 *
 * The nightly bar refresh fetches ~500 symbols at the rate limiter's 0.6 s,
 * which is twelve minutes. For twelve minutes "it is working" and "it has
 * hung" look identical if all the screen says is the name of the job -- which
 * is the whole reason this exists.
 *
 * `frontend/CLAUDE.md` §3 applies throughout:
 *
 * - **No progress is not zero progress.** The server sends `progress: null`
 *   whenever no long job is running, and this renders NOTHING in that case
 *   rather than an empty bar, which would read as a job stuck at 0%.
 * - **A total of zero is a real answer.** "Nothing to refresh" is an outcome,
 *   and it gets a determinate full bar with that wording, not a division by
 *   zero and not a spinner forever.
 * - **The estimate is labelled an estimate**, and is withheld until there is
 *   enough of the run to extrapolate from. Two symbols into five hundred, the
 *   arithmetic works and the answer is worthless.
 * - **A stalled poll is visible.** The elapsed clock ticks LOCALLY off the
 *   server's own `startedAtIst` (the Settings-page trick, §4), so a job whose
 *   updates have stopped shows a clock running on past a bar that is not
 *   moving, instead of freezing and looking fine.
 *
 * Both the Live tab's status strip and the Health tab render this, from the
 * same field on their own payloads. One component so the two cannot drift into
 * describing the same run differently.
 */

/** Below this fraction an extrapolated finish time is noise, not an estimate. */
const ENOUGH_TO_EXTRAPOLATE = 0.04;

function humanDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return `${minutes}m ${whole % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export default function JobProgress({ progress, activity, compact = false }) {
  // One local tick a second so elapsed and the estimate move between the
  // page's polls. Hooks run unconditionally, so the null check is below them.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (!progress) return null;

  const { done, total, item, startedAtIst } = progress;
  const hasTotal = typeof total === 'number' && total > 0;
  const fraction = hasTotal ? Math.min(1, Math.max(0, done / total)) : null;

  const startedMs = startedAtIst ? Date.parse(startedAtIst) : NaN;
  const elapsedSeconds = Number.isNaN(startedMs) ? null : (now - startedMs) / 1000;

  // Extrapolated from what this run has actually done, not from a configured
  // rate: the rate limiter, the network and the number of symbols that were
  // already current all move it, and only the run itself knows.
  let remaining = null;
  if (
    fraction !== null &&
    fraction >= ENOUGH_TO_EXTRAPOLATE &&
    fraction < 1 &&
    elapsedSeconds !== null &&
    elapsedSeconds > 0
  ) {
    remaining = humanDuration((elapsedSeconds / fraction) * (1 - fraction));
  }

  const percent = fraction === null ? null : Math.floor(fraction * 100);
  const finished = fraction === 1;

  const headline = (() => {
    if (hasTotal && total === 0) return 'nothing to do';
    if (finished) return 'finishing up';
    if (percent === null) return 'in progress';
    return `${percent}%`;
  })();

  const detail = (() => {
    if (!hasTotal) return 'The total is not known yet.';
    if (finished) return `${done} of ${total} done.`;
    return `${done} of ${total}${item ? ` — ${item}` : ''}`;
  })();

  return (
    <Box sx={{ width: '100%', mt: compact ? 1 : 1.5 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="baseline"
        spacing={2}
        sx={{ mb: 0.5 }}
      >
        <Typography variant="caption" color="text.secondary" noWrap>
          {activity ? `${activity} — ` : ''}
          <strong>{headline}</strong>
        </Typography>
        <Typography
          variant="caption"
          color="text.disabled"
          className="numeric"
          noWrap
        >
          {detail}
        </Typography>
      </Stack>

      {/* Indeterminate only when the total genuinely is not known. A
          determinate bar at 0% would be a claim about how much is left. */}
      <Tooltip
        title={
          hasTotal
            ? `${done} of ${total} symbols. The estimate is extrapolated from this run's own pace, not from a configured rate.`
            : 'The total is not known yet, so how far through this is cannot be shown.'
        }
      >
        <LinearProgress
          variant={hasTotal ? 'determinate' : 'indeterminate'}
          value={hasTotal ? (fraction ?? 0) * 100 : undefined}
          sx={{ height: 6, borderRadius: 3 }}
        />
      </Tooltip>

      <Stack
        direction="row"
        justifyContent="space-between"
        spacing={2}
        sx={{ mt: 0.5 }}
      >
        <Typography variant="caption" color="text.disabled" className="numeric">
          {elapsedSeconds === null
            ? 'running'
            : `${humanDuration(elapsedSeconds)} elapsed`}
        </Typography>
        <Typography variant="caption" color="text.disabled" className="numeric">
          {remaining
            ? `about ${remaining} left (estimate)`
            : finished
              ? 'done'
              : 'too early to estimate'}
        </Typography>
      </Stack>
    </Box>
  );
}
