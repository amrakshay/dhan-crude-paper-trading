import { alpha } from '@mui/material/styles';

/**
 * Readable colour for a `<Chip>`, as `sx`.
 *
 * **`MuiChip` sets a background on every chip in this theme** (see
 * `frontend/CLAUDE.md` section 1), which defeats `color="success"` and friends:
 * the text takes the palette colour, the background stays the theme's grey, and
 * what ships is a chip nobody can read. That already happened once with a
 * capability chip.
 *
 * So a chip that needs to stand out sets `bgcolor` and `color` explicitly. This
 * is the one place that decides what each tone looks like, so the IPO tables
 * and the Status tab cannot drift into two different greens.
 *
 * Tones are named for what they MEAN, not for a colour: `done` is a finished
 * step, `pending` is one still outstanding and nobody's fault yet, `problem`
 * needs a person. `neutral` is the plain outlined chip.
 */
export function chipTone(theme, tone) {
  const tones = {
    done: { bgcolor: theme.market.upSoft, color: theme.market.up },
    pending: {
      bgcolor: alpha(theme.palette.warning.main, 0.14),
      color: theme.palette.warning.main,
    },
    problem: { bgcolor: theme.market.downSoft, color: theme.market.down },
    neutral: { bgcolor: 'transparent', color: 'text.secondary' },
  };
  return {
    ...(tones[tone] ?? tones.neutral),
    borderColor: 'divider',
    fontWeight: 600,
  };
}
