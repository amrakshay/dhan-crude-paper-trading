import {
  Box,
  Button,
  Chip,
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
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { ipoSourceUrl } from '../api/ipo';
import { chipTone } from '../theme/chipTone';

/**
 * One tab's table. Three tabs, one component, because the rows are the same
 * object seen at three points in its life.
 *
 * `frontend/CLAUDE.md` section 3's honesty rules, applied here:
 *
 * - **Unknown is not zero.** A GMP the source does not publish renders as "not
 *   published", never ₹0. The API returns `null` for it deliberately.
 * - **Every GMP says how old it is**, and a stale one says so in the colour of
 *   a warning. A premium with no age would let yesterday's number read as this
 *   morning's, which is the one failure this page must not have.
 * - **The buttons are HIDDEN for a ROLE_USER, not disabled.** Offering a
 *   control that will be refused is worse than not offering it. The API
 *   refuses it independently either way.
 */

const IST = 'Asia/Kolkata';

function formatIst(iso, { withDate = true } = {}) {
  if (!iso) return '—';
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '—';
  return when.toLocaleString('en-IN', {
    timeZone: IST,
    day: '2-digit',
    month: 'short',
    ...(withDate ? {} : {}),
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function formatDay(day) {
  if (!day) return '—';
  const when = new Date(`${day}T00:00:00`);
  if (Number.isNaN(when.getTime())) return day;
  return when.toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

function money(value, { signed = false } = {}) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  if (Number.isNaN(number)) return null;
  const sign = signed && number > 0 ? '+' : '';
  return `${sign}₹${number.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function percent(value, { signed = false } = {}) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  if (Number.isNaN(number)) return null;
  const sign = signed && number > 0 ? '+' : '';
  return `${sign}${number.toFixed(2)}%`;
}

function GmpCell({ gmp }) {
  const value = money(gmp.gmp);
  const pct = percent(gmp.gmpPercent);
  // The SOURCE's own timestamp is when the premium was observed; ours is when
  // this application last read it. The first is the honest answer to "how old
  // is this number", so it is what is shown, and the tooltip carries both.
  const captured = gmp.sourceUpdatedAt || gmp.capturedAt;

  return (
    <Stack spacing={0.25}>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {value ?? 'not published'}
        {pct ? (
          <Typography component="span" variant="caption" sx={{ ml: 0.75, opacity: 0.75 }}>
            ({pct} of issue price)
          </Typography>
        ) : null}
      </Typography>
      <Tooltip
        title={
          gmp.staleReason
            ? gmp.staleReason
            : `Source last updated ${formatIst(gmp.sourceUpdatedAt)}; fetched ${formatIst(
                gmp.capturedAt,
              )}`
        }
      >
        <Typography
          variant="caption"
          color={gmp.isStale ? 'warning.main' : 'text.secondary'}
          sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}
        >
          {gmp.isStale ? <WarningAmberIcon sx={{ fontSize: 14 }} /> : null}
          {gmp.isStale ? 'STALE · ' : ''}
          {formatIst(captured)}
        </Typography>
      </Tooltip>
    </Stack>
  );
}

function ActionButtons({ ipo, busy, onAction }) {
  return (
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
      <Button
        size="small"
        variant={ipo.applied ? 'contained' : 'outlined'}
        color={ipo.applied ? 'success' : 'inherit'}
        disabled={busy}
        onClick={() => onAction(ipo, 'APPLIED', !ipo.applied)}
      >
        {ipo.applied ? 'Applied ✓' : 'Applied'}
      </Button>
      <Button
        size="small"
        variant={ipo.mandateAccepted ? 'contained' : 'outlined'}
        color={ipo.mandateAccepted ? 'success' : 'inherit'}
        disabled={busy}
        onClick={() => onAction(ipo, 'MANDATE_ACCEPTED', !ipo.mandateAccepted)}
      >
        {ipo.mandateAccepted ? 'Mandate accepted ✓' : 'Accept UPI mandate'}
      </Button>
      <Button
        size="small"
        variant={ipo.rejected ? 'contained' : 'outlined'}
        color={ipo.rejected ? 'error' : 'inherit'}
        disabled={busy}
        onClick={() => onAction(ipo, 'REJECTED', !ipo.rejected)}
      >
        {ipo.rejected ? 'Rejected — undo' : 'Reject'}
      </Button>
    </Stack>
  );
}

function StateChips({ ipo }) {
  const theme = useTheme();
  // Explicit colours throughout: this theme gives every chip a background, so
  // `color="success"` ships as unreadable text on grey.
  if (ipo.rejected) {
    return (
      <Chip
        size="small"
        variant="outlined"
        sx={chipTone(theme, 'neutral')}
        label="Rejected — no reminders"
      />
    );
  }
  if (ipo.applied && ipo.mandateAccepted) {
    return (
      <Chip
        size="small"
        variant="outlined"
        sx={chipTone(theme, 'done')}
        label="Done — no reminders"
      />
    );
  }
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      <Chip
        size="small"
        variant="outlined"
        sx={chipTone(theme, ipo.applied ? 'done' : 'pending')}
        label={ipo.applied ? `Applied ${formatIst(ipo.appliedAt)}` : 'Not applied'}
      />
      <Chip
        size="small"
        variant="outlined"
        sx={chipTone(theme, 'pending')}
        label="Mandate not accepted"
      />
    </Stack>
  );
}

export default function IpoTable({ tab, ipos, isAdmin, busyId, onAction, emptyMessage }) {
  const theme = useTheme();
  const showActions = tab === 'closing-today';
  const showListing = tab === 'listed';

  if (!ipos || ipos.length === 0) {
    return (
      <Box sx={{ py: 6, textAlign: 'center' }}>
        <Typography variant="body2" color="text.secondary">
          {emptyMessage}
        </Typography>
      </Box>
    );
  }

  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Company</TableCell>
            <TableCell align="right">GMP</TableCell>
            <TableCell align="right">Issue price</TableCell>
            <TableCell align="right">Lot</TableCell>
            {showListing ? <TableCell align="right">Listing price</TableCell> : null}
            {showListing ? <TableCell align="right">Listing gain</TableCell> : null}
            {showListing ? <TableCell>Listed</TableCell> : <TableCell>Closes</TableCell>}
            <TableCell>{showListing ? 'Did I apply?' : 'State'}</TableCell>
            {showActions && isAdmin ? <TableCell>Actions</TableCell> : null}
          </TableRow>
        </TableHead>
        <TableBody>
          {ipos.map((ipo) => {
            const gain = money(ipo.listingGain, { signed: true });
            const gainPct = percent(ipo.listingGainPercent, { signed: true });
            const gainNumber = Number(ipo.listingGain);
            return (
              <TableRow key={ipo.id} hover>
                <TableCell>
                  <MuiLink
                    href={ipoSourceUrl(ipo.sourcePath)}
                    target="_blank"
                    rel="noreferrer"
                    underline="hover"
                  >
                    {ipo.companyName}
                  </MuiLink>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Mainboard · first seen {formatIst(ipo.firstSeenAt)}
                  </Typography>
                </TableCell>
                <TableCell align="right">
                  <GmpCell gmp={ipo.gmp} />
                </TableCell>
                <TableCell align="right">{money(ipo.issuePrice) ?? '—'}</TableCell>
                <TableCell align="right">{ipo.lotSize ?? '—'}</TableCell>
                {showListing ? (
                  <TableCell align="right">{money(ipo.listingPrice) ?? '—'}</TableCell>
                ) : null}
                {showListing ? (
                  <TableCell align="right">
                    {gain ? (
                      <Typography
                        variant="body2"
                        color={gainNumber >= 0 ? 'success.main' : 'error.main'}
                        sx={{ fontWeight: 600 }}
                      >
                        {gain}
                        <Typography component="span" variant="caption" sx={{ ml: 0.5 }}>
                          ({gainPct})
                        </Typography>
                      </Typography>
                    ) : (
                      // Not zero: a listing price with no issue price cannot
                      // produce a gain, and 0.00 would be a claim.
                      <Typography variant="caption" color="text.secondary">
                        not measurable
                      </Typography>
                    )}
                  </TableCell>
                ) : null}
                <TableCell>
                  {formatDay(showListing ? ipo.listingDate : ipo.closeDate)}
                </TableCell>
                <TableCell>
                  {showListing ? (
                    <Chip
                      size="small"
                      variant="outlined"
                      sx={chipTone(
                        theme,
                        ipo.rejected ? 'neutral' : ipo.applied ? 'done' : 'pending',
                      )}
                      label={
                        ipo.applied
                          ? ipo.mandateAccepted
                            ? 'Applied'
                            : 'Applied, mandate never accepted'
                          : ipo.rejected
                            ? 'Passed on it'
                            : 'Did not apply'
                      }
                    />
                  ) : (
                    <StateChips ipo={ipo} />
                  )}
                </TableCell>
                {showActions && isAdmin ? (
                  <TableCell>
                    <ActionButtons
                      ipo={ipo}
                      busy={busyId === ipo.id}
                      onAction={onAction}
                    />
                  </TableCell>
                ) : null}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
