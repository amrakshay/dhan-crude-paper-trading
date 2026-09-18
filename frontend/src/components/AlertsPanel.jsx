import { useEffect, useMemo, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  AlertTitle,
  Box,
  Chip,
  CircularProgress,
  Divider,
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
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';

/**
 * The Alerts tab, rendered identically on the System Health page and inside a
 * strategy's own page.
 *
 * **ONE component, used by both**, the same way `JobProgress` is shared by the
 * swing page's Live and Health tabs. The two surfaces answer different
 * questions -- "what will this process tell me about" and "what will this
 * strategy tell me about" -- but they are the same list filtered, and two
 * components would drift into describing the same rule differently.
 *
 * **Read-only, deliberately.** There is no edit and no delete. What gets
 * alerted is a property of the build, decided in code and reviewed like code --
 * not a preference. The panel says so rather than leaving somebody hunting for
 * a switch that does not exist.
 *
 * Section 3's honesty rules apply throughout:
 *
 * - **Never fired is not zero.** A rule with no stored row says "never", not a
 *   timestamp and not a count of nothing.
 * - **Recorded is not delivered.** A fact is written down even when nothing is
 *   configured to carry it, so the panel reports the two separately. A page
 *   that conflated them would show a healthy list while every message was being
 *   dropped on the floor.
 * - **A collapsed flood says how many it stood for**, because a repeat that
 *   arrives as one line looks like a single event.
 */

const SEVERITY_ORDER = { CRITICAL: 0, ERROR: 1, WARNING: 2, INFO: 3 };

function SeverityChip({ severity }) {
  const theme = useTheme();
  const tone =
    {
      CRITICAL: { bg: theme.market.downSoft, fg: theme.market.down },
      ERROR: { bg: theme.market.downSoft, fg: theme.market.down },
      WARNING: { bg: theme.palette.warning.light, fg: theme.palette.warning.dark },
      INFO: { bg: theme.palette.action.hover, fg: theme.palette.text.secondary },
    }[severity] ?? { bg: theme.palette.action.hover, fg: theme.palette.text.secondary };

  return (
    <Chip
      size="small"
      label={severity}
      // MuiChip sets a background on every chip in this theme, so bgcolor and
      // color are set explicitly (frontend/CLAUDE.md §1).
      sx={{ bgcolor: tone.bg, color: tone.fg, fontWeight: 600, height: 20, fontSize: 11 }}
    />
  );
}

function StatusChip({ status }) {
  const theme = useTheme();
  if (!status) return null;
  const tone =
    {
      SENT: { bg: theme.market.upSoft, fg: theme.market.up },
      PENDING: { bg: theme.palette.action.hover, fg: theme.palette.text.secondary },
      FAILED: { bg: theme.market.downSoft, fg: theme.market.down },
      SUPPRESSED: { bg: theme.palette.warning.light, fg: theme.palette.warning.dark },
    }[status] ?? { bg: theme.palette.action.hover, fg: theme.palette.text.secondary };
  return (
    <Chip
      size="small"
      label={status}
      sx={{ bgcolor: tone.bg, color: tone.fg, height: 20, fontSize: 11 }}
    />
  );
}

/** "never", or how long ago — ticking locally so a stale poll is visible. */
function firedLabel(iso, now) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function Never({ children = 'never' }) {
  return (
    <Typography variant="body2" color="text.disabled" component="span">
      {children}
    </Typography>
  );
}

function DeliveryBanner({ delivery }) {
  if (!delivery) return null;

  // Recording and delivering are different things, and the difference is the
  // whole point of the outbox.
  if (!delivery.telegramConfigured) {
    return (
      <Alert severity="info">
        <AlertTitle>Alerts are being recorded, but nothing is carrying them</AlertTitle>
        Every rule below still writes its row, so the history is intact — they are
        marked <strong>SUPPRESSED</strong> with the reason rather than lost. Configure a
        Telegram connection to have them delivered.
      </Alert>
    );
  }
  if (!delivery.telegramEnabled) {
    return (
      <Alert severity="warning">
        <AlertTitle>The Telegram connection is switched off</AlertTitle>
        Alerts are still recorded and marked SUPPRESSED; nothing is being sent.
      </Alert>
    );
  }
  if (!delivery.dispatcherRunning) {
    return (
      <Alert severity="error">
        <AlertTitle>The alert dispatcher is not running</AlertTitle>
        Alerts are being written to the outbox and nothing is draining it, so they will
        queue rather than arrive.
      </Alert>
    );
  }
  return (
    <Alert severity="success">
      Delivering to <strong>{delivery.channel}</strong>
      {delivery.watcherRunning
        ? '. The health watcher is running.'
        : '. The health watcher is NOT running, so the system-health rules below are not being evaluated.'}
    </Alert>
  );
}

export default function AlertsPanel({ catalogue, error, loading, strategyKey }) {
  const [now, setNow] = useState(() => Date.now());

  // The age ticks locally off the absolute timestamp, so a rule that has not
  // fired again visibly ages rather than freezing between polls.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const grouped = useMemo(() => {
    const rules = catalogue?.rules ?? [];
    const byCategory = new Map();
    rules.forEach((rule) => {
      if (!byCategory.has(rule.category)) byCategory.set(rule.category, []);
      byCategory.get(rule.category).push(rule);
    });
    byCategory.forEach((list) =>
      list.sort(
        (a, b) =>
          (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
      ),
    );
    return [...byCategory.entries()];
  }, [catalogue]);

  if (error) {
    return <Alert severity="error">{error}</Alert>;
  }
  if (!catalogue) {
    return loading ? (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 200 }}>
        <CircularProgress size={22} />
      </Box>
    ) : null;
  }

  const counts = catalogue.counts ?? {};
  const sink = catalogue.delivery?.sink;

  return (
    <Stack spacing={2}>
      <DeliveryBanner delivery={catalogue.delivery} />

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {['SENT', 'PENDING', 'FAILED', 'SUPPRESSED'].map((status) => (
          <Chip
            key={status}
            size="small"
            variant="outlined"
            label={`${status} ${counts[status] ?? 0}`}
            sx={{ height: 22 }}
          />
        ))}
        <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'center' }}>
          in the last 24 hours
        </Typography>
      </Stack>

      {/* A dropped record is a real signal: it only happens during a flood. */}
      {sink && sink.dropped > 0 ? (
        <Alert severity="warning">
          The error sink dropped <strong>{sink.dropped}</strong> record(s) because its
          queue was full. That only happens during a flood — the alerts that did get
          through are the ones below.
        </Alert>
      ) : null}

      {grouped.map(([category, rules]) => (
        <Box key={category}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
            {rules[0]?.categoryLabel ?? category}
          </Typography>
          <Stack spacing={1}>
            {rules.map((rule) => {
              const fired = firedLabel(rule.lastFiredAt, now);
              return (
                <Accordion key={rule.key} disableGutters>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Stack
                      direction="row"
                      spacing={1.5}
                      alignItems="center"
                      sx={{ width: '100%', pr: 1 }}
                      flexWrap="wrap"
                      useFlexGap
                    >
                      <SeverityChip severity={rule.severity} />
                      <Typography variant="body2" sx={{ flex: 1, minWidth: 180 }}>
                        {rule.title}
                      </Typography>
                      <StatusChip status={rule.lastStatus} />
                      <Tooltip
                        title={
                          rule.lastFiredAt
                            ? new Date(rule.lastFiredAt).toLocaleString('en-IN', {
                                hour12: false,
                              })
                            : 'This alert has never fired on this installation'
                        }
                      >
                        <Typography variant="caption" color="text.secondary">
                          {/* Never fired is not zero. */}
                          {fired ?? <Never />}
                        </Typography>
                      </Tooltip>
                    </Stack>
                  </AccordionSummary>
                  <AccordionDetails>
                    <Stack spacing={1.5}>
                      <Box>
                        <Typography variant="caption" color="text.secondary">
                          What makes it fire
                        </Typography>
                        <Typography variant="body2">{rule.trigger}</Typography>
                      </Box>
                      <Box>
                        <Typography variant="caption" color="text.secondary">
                          Why it is worth a message
                        </Typography>
                        <Typography variant="body2">{rule.why}</Typography>
                      </Box>
                      {rule.collapsing ? (
                        <Box>
                          <Typography variant="caption" color="text.secondary">
                            Repeats
                          </Typography>
                          <Typography variant="body2">{rule.collapsing}</Typography>
                        </Box>
                      ) : null}
                      {rule.caveat ? (
                        <Box>
                          <Typography variant="caption" color="text.secondary">
                            Worth knowing
                          </Typography>
                          <Typography variant="body2">{rule.caveat}</Typography>
                        </Box>
                      ) : null}
                      <Divider />
                      <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
                        <Box>
                          <Typography variant="caption" color="text.secondary" display="block">
                            Last fired
                          </Typography>
                          <Typography variant="body2">
                            {rule.lastFiredAt ? (
                              new Date(rule.lastFiredAt).toLocaleString('en-IN', {
                                hour12: false,
                              })
                            ) : (
                              <Never />
                            )}
                          </Typography>
                        </Box>
                        <Box>
                          <Typography variant="caption" color="text.secondary" display="block">
                            Times fired
                          </Typography>
                          <Typography variant="body2" className="numeric">
                            {rule.timesFired}
                          </Typography>
                        </Box>
                        {rule.timesCollapsed > 0 ? (
                          <Box>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              display="block"
                            >
                              Repeats collapsed
                            </Typography>
                            <Typography variant="body2" className="numeric">
                              {rule.timesCollapsed}
                            </Typography>
                          </Box>
                        ) : null}
                      </Stack>
                    </Stack>
                  </AccordionDetails>
                </Accordion>
              );
            })}
          </Stack>
        </Box>
      ))}

      {/* The outbox itself: the answer to "did it arrive". */}
      <Box>
        <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
          Recent alerts
          {strategyKey ? ` for ${strategyKey}` : ''}
        </Typography>
        {(catalogue.recent ?? []).length === 0 ? (
          <Typography variant="body2" color="text.disabled">
            Nothing has been raised yet.
          </Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>When</TableCell>
                  <TableCell>Alert</TableCell>
                  <TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {catalogue.recent.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      <Typography variant="caption">
                        {new Date(row.createdAt).toLocaleString('en-IN', { hour12: false })}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{row.title}</Typography>
                      {row.suppressedCount > 0 ? (
                        <Typography variant="caption" color="text.secondary">
                          stood in for {row.suppressedCount} further occurrence
                          {row.suppressedCount === 1 ? '' : 's'}
                        </Typography>
                      ) : null}
                      {row.lastError ? (
                        <Typography variant="caption" color="text.secondary" display="block">
                          {row.lastError}
                        </Typography>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <StatusChip status={row.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Box>

      {/* What this page cannot tell you, said rather than implied away. */}
      <Alert severity="info" icon={<InfoOutlinedIcon />}>
        <Stack spacing={0.5}>
          {(catalogue.notes ?? []).map((note) => (
            <Typography key={note} variant="caption">
              {note}
            </Typography>
          ))}
        </Stack>
      </Alert>
    </Stack>
  );
}
