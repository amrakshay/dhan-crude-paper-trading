import { Box, LinearProgress, Stack, Typography } from '@mui/material';

/**
 * The filter funnel: how far the universe got through B2–B8.
 *
 * **This is what makes "nothing qualified" readable**, which matters more for
 * this strategy than for anything else in the application. At about half a
 * signal a session the overwhelmingly normal outcome is that nothing
 * qualified, and a page that could only say so would look broken far more
 * often than it looked right. "289 tradable, 284 measured, 31 above their
 * 55-day high, 6 on 2× volume, 0 closing strong" reads as a working scan; a
 * bare zero does not.
 *
 * Its own component because BOTH the Live tab's history and the Signals tab
 * render it, from the same server-side stage list — two copies would drift
 * into describing the same filters differently.
 *
 * A stage with a null count is LEFT OUT rather than drawn as zero: an older
 * record that predates a stage did not measure it, which is not the same as
 * measuring none of it.
 */
export default function BtstFunnel({ funnel }) {
  const stages = (funnel || []).filter(
    (one) => one.count !== null && one.count !== undefined,
  );
  if (!stages.length) return null;
  const top = Math.max(...stages.map((one) => one.count), 1);

  return (
    <Box sx={{ mt: 1 }}>
      {stages.map((one) => (
        <Stack
          key={one.key}
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mb: 0.25 }}
        >
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ minWidth: { xs: 140, sm: 230 }, flexShrink: 0 }}
          >
            {one.label}
          </Typography>
          <LinearProgress
            variant="determinate"
            value={Math.min((one.count / top) * 100, 100)}
            sx={{ flexGrow: 1, height: 6, borderRadius: 3 }}
          />
          <Typography
            variant="caption"
            className="numeric"
            sx={{ minWidth: 44, textAlign: 'right' }}
          >
            {one.count}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
}
