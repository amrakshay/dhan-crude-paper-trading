import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Card,
  Tab,
  Tabs,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  LinearProgress,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import RefreshIcon from '@mui/icons-material/Refresh';
import ScienceIcon from '@mui/icons-material/Science';
import { useTheme } from '@mui/material/styles';
import { useSearchParams } from 'react-router-dom';
import { healthApi } from '../api/health';
import { connectionsApi } from '../api/connections';
import AlertsPanel from '../components/AlertsPanel';
import SyntheticBanner from '../components/SyntheticBanner';
import {
  formatAge,
  formatBytes,
  formatCompact,
  formatCountdown,
  formatDuration,
  formatQty,
  formatTime,
} from '../utils/format';

/**
 * How often the page re-reads the endpoint. Slower than Positions (2 s) on
 * purpose: nothing on this page changes meaningfully faster, and a health page
 * that is itself a load source is reporting on a system it distorted. The
 * endpoint is in `LogRequestsMiddleware.IGNORED_PATHS`, so polling it does not
 * fill the access log this page reports on.
 */
const POLL_INTERVAL_MS = 5000;

/** Label / value row. Used everywhere instead of ad-hoc Typography pairs. */
function Field({ label, value, hint, mono = false, color }) {
  return (
    <Stack direction="row" spacing={2} justifyContent="space-between" alignItems="baseline">
      <Tooltip title={hint || ''} placement="left">
        <Typography variant="body2" color="text.secondary" sx={{ flexShrink: 0 }}>
          {label}
        </Typography>
      </Tooltip>
      <Typography
        variant="body2"
        className={mono ? 'numeric' : undefined}
        sx={{ textAlign: 'right', wordBreak: 'break-all', color }}
      >
        {value ?? '—'}
      </Typography>
    </Stack>
  );
}

function SectionCard({ title, subtitle, action, children }) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          spacing={1}
          sx={{ mb: subtitle ? 0.5 : 1.5 }}
        >
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            {title}
          </Typography>
          {action}
        </Stack>
        {subtitle ? (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
            {subtitle}
          </Typography>
        ) : null}
        <Stack spacing={0.75}>{children}</Stack>
      </CardContent>
    </Card>
  );
}

function StateChip({ state }) {
  const theme = useTheme();
  const palette = {
    running: { label: 'running', color: theme.market.up },
    missing: { label: 'not running', color: theme.market.down },
    unexpected: { label: 'unexpected', color: theme.palette.warning.main },
  }[state] ?? { label: state, color: theme.palette.text.secondary };

  return (
    <Chip
      size="small"
      label={palette.label}
      variant="outlined"
      sx={{ height: 20, borderColor: palette.color, color: palette.color }}
    />
  );
}

/**
 * The upstream feed's silence, read against the cliff it is heading for.
 *
 * Dhan drops a connection after `server_inactivity_timeout_seconds` of
 * silence, which is what the `dhan-feed-watchdog` task exists to prevent. The
 * raw age is an integer with no meaning attached; the headroom is the number
 * an operator can act on.
 */
function InactivityHeadroom({ feed }) {
  const theme = useTheme();
  if (!feed.hasUpstreamConnection) return null;
  if (feed.inactivityHeadroomMs === null || feed.inactivityHeadroomMs === undefined) {
    return <Field label="Headroom to Dhan's drop" value="no message received yet" />;
  }

  const timeoutMs = (feed.inactivityTimeoutSeconds ?? 40) * 1000;
  const fraction = Math.max(0, Math.min(1, feed.inactivityHeadroomMs / timeoutMs));
  const critical = fraction < 0.25;
  const color = critical ? theme.market.down : theme.market.up;

  return (
    <Stack spacing={0.75} sx={{ pt: 0.5 }}>
      <Field
        label="Headroom to Dhan's drop"
        mono
        color={color}
        value={`${formatAge(feed.inactivityHeadroomMs)} of ${feed.inactivityTimeoutSeconds}s`}
        hint="Dhan closes a connection that has been silent this long. The watchdog task reconnects, but a shrinking headroom is the earliest warning there is."
      />
      <LinearProgress
        variant="determinate"
        value={fraction * 100}
        sx={{
          height: 6,
          borderRadius: 3,
          bgcolor: 'action.hover',
          '& .MuiLinearProgress-bar': { bgcolor: color },
        }}
      />
    </Stack>
  );
}

export default function SystemHealthPage() {
  const theme = useTheme();
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [fetchedAt, setFetchedAt] = useState(null);
  // Re-renders once a second so the "as of" age moves without re-fetching,
  // the same trick the Settings page's token countdown uses.
  const [now, setNow] = useState(() => Date.now());
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const payload = await healthApi.get();
      setHealth(payload);
      setFetchedAt(Date.now());
      setError(null);
    } catch (exc) {
      setError(exc.message || 'Could not read system health');
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(load, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const asOfMs = fetchedAt ? now - fetchedAt : null;

  // The tab lives in the URL so "read this page" is a link somebody can send,
  // the same way the Swing Momentum page does it.
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'alerts' ? 'alerts' : 'process';

  // The catalogue is fetched on the SAME poll as the rest of the page rather
  // than on a second timer. A page that reports on load must not be a load
  // source (frontend/CLAUDE.md §5c), and its failure is kept off the page's
  // `error` state so a hiccup here cannot blank the process view.
  const [catalogue, setCatalogue] = useState(null);
  const [catalogueError, setCatalogueError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const read = () => {
      connectionsApi
        .catalogue()
        .then((next) => {
          if (!cancelled) {
            setCatalogue(next);
            setCatalogueError(null);
          }
        })
        .catch((readError) => {
          if (!cancelled) setCatalogueError(readError.message);
        });
    };
    read();
    if (!autoRefresh) return () => { cancelled = true; };
    const timer = window.setInterval(read, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [autoRefresh]);

  const summary = health?.summary;
  const process = health?.process;
  const tasks = health?.tasks;
  const feed = health?.upstreamFeed;
  const sockets = health?.browserSockets;
  const dhan = health?.dhanApi;
  const credentials = health?.credentials;
  const freshness = health?.dataFreshness;
  const problems = health?.problems;
  const workers = health?.workers;

  const sortedTasks = useMemo(() => {
    if (!tasks?.tasks) return [];
    // Anything wrong floats to the top; the rest keep their declared order.
    const rank = { missing: 0, unexpected: 1, running: 2 };
    return [...tasks.tasks].sort((a, b) => (rank[a.state] ?? 3) - (rank[b.state] ?? 3));
  }, [tasks]);

  if (loading && !health) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Stack spacing={2}>
      <SyntheticBanner />

      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
        <Box>
          <Typography variant="h5">System Health</Typography>
          <Typography variant="caption" color="text.secondary">
            {fetchedAt
              ? `as of ${formatAge(asOfMs)} ago · ${formatTime(fetchedAt)}`
              : 'never loaded'}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={autoRefresh}
                onChange={(event) => setAutoRefresh(event.target.checked)}
              />
            }
            label={
              <Typography variant="caption" color="text.secondary">
                auto every {POLL_INTERVAL_MS / 1000}s
              </Typography>
            }
          />
          <Button size="small" startIcon={<RefreshIcon />} onClick={load} variant="outlined">
            Refresh
          </Button>
        </Stack>
      </Stack>

      {error ? (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      ) : null}

      <Tabs
        value={tab}
        onChange={(event, next) =>
          setParams(next === 'process' ? {} : { tab: next }, { replace: true })
        }
        sx={{ borderBottom: 1, borderColor: 'divider' }}
      >
        <Tab value="process" label="Process" />
        <Tab value="alerts" label="Alerts" />
      </Tabs>

      {tab === 'alerts' ? (
        <AlertsPanel
          catalogue={catalogue}
          error={catalogueError}
          loading={loading}
        />
      ) : (
        <>

      {summary ? (
        <Alert
          severity={summary.healthy ? 'success' : 'warning'}
          icon={summary.healthy ? <CheckCircleIcon /> : <ErrorOutlineIcon />}
        >
          <AlertTitle>
            {summary.healthy
              ? 'Everything this process can check looks healthy'
              : `${summary.problems.length} thing${
                  summary.problems.length === 1 ? '' : 's'
                } worth looking at`}
          </AlertTitle>
          {summary.healthy ? (
            <Typography variant="body2">
              This is what the process can see about itself. It cannot see other processes using
              the same Dhan credentials, and it says nothing about whether the prices are correct
              — only that they are arriving.
            </Typography>
          ) : (
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {summary.problems.map((problem) => (
                <li key={problem}>
                  <Typography variant="body2">{problem}</Typography>
                </li>
              ))}
            </Box>
          )}
        </Alert>
      ) : null}

      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)', xl: 'repeat(3, 1fr)' },
        }}
      >
        {/* --- process ----------------------------------------------------- */}
        {process ? (
          <SectionCard title="Process" subtitle={process.concurrencyModel}>
            <Field label="Uptime" mono value={formatDuration(process.uptimeSeconds)} />
            <Field label="Started" value={formatTime(process.startedAtMs)} />
            <Field label="PID" mono value={process.pid} />
            <Field label="Uvicorn workers" mono value={process.workers} />
            <Field label="Python" value={process.pythonVersion} />
            <Field label="Platform" value={process.platform} />
            <Divider sx={{ my: 0.5 }} />
            <Field label="Config path" value={process.configPath} />
            <Field label="Log directory" value={process.logDir} />
            <Field label="Log level" value={process.logLevel} />
            <Field label="Static dir" value={process.staticDir ?? 'not serving the UI'} />
            <Divider sx={{ my: 0.5 }} />
            <Field label="Database" value={process.database?.backend} />
            <Field label="Connection" value={process.database?.urlRedacted} />
            <Field
              label="Schema revision"
              mono
              value={process.database?.schemaRevision ?? 'no alembic_version row'}
              hint="The Alembic revision this database is actually at. Absent when the schema was built by create_tables() rather than by migrating."
            />
            <Field
              label="Pooling"
              value={process.database?.pooling}
              hint="SQLite uses a NullPool, so there is no pool to report."
            />
            <Divider sx={{ my: 0.5 }} />
            {process.resources?.available ? (
              <>
                <Field label="Resident memory" mono value={formatBytes(process.resources.rssBytes)} />
                <Field
                  label="CPU"
                  mono
                  value={
                    process.resources.cpuPercent === null
                      ? 'measuring…'
                      : `${process.resources.cpuPercent.toFixed(1)}%`
                  }
                  hint="Averaged over the interval since the previous poll. The first reading has nothing to difference against, so it is not shown."
                />
                <Field label="Open file descriptors" mono value={process.resources.openFileDescriptors} />
                <Field
                  label="OS threads"
                  mono
                  value={process.resources.osThreads}
                  hint="Interpreter and libc threads. This application has no thread pool — its concurrency is the asyncio tasks."
                />
              </>
            ) : (
              <Typography variant="caption" color="text.secondary">
                Memory and CPU figures are unavailable: {process.resources?.unavailableReason}
              </Typography>
            )}
          </SectionCard>
        ) : null}

        {/* --- upstream feed ------------------------------------------------ */}
        {feed ? (
          <SectionCard
            title="Upstream feed"
            subtitle={
              feed.hasUpstreamConnection
                ? 'One WebSocket to Dhan, shared by every browser tab'
                : 'Synthetic mode'
            }
            action={
              feed.synthetic ? (
                <Chip
                  size="small"
                  icon={<ScienceIcon sx={{ fontSize: 14 }} />}
                  label="synthetic"
                  sx={{ height: 22, bgcolor: theme.market.itm }}
                />
              ) : (
                <StateChip state={feed.state === 'CONNECTED' ? 'running' : 'missing'} />
              )
            }
          >
            {feed.upstreamNote ? (
              <Alert severity="info" sx={{ mb: 1 }}>
                {feed.upstreamNote}
              </Alert>
            ) : null}
            <Field label="State" value={feed.state} />
            {feed.detail ? <Field label="Detail" value={feed.detail} /> : null}
            {feed.hasUpstreamConnection ? (
              <>
                <Field label="Mode" value={`${feed.mode} (requestCode ${feed.requestCode})`} />
                <Field label="Connected at" value={formatTime(feed.connectedAtMs)} />
                <Field label="Last message" mono value={formatAge(feed.lastMessageAgeMs)} />
                <InactivityHeadroom feed={feed} />
                <Divider sx={{ my: 0.5 }} />
                <Field
                  label="Connections used"
                  mono
                  value={`${feed.connectionsUsedByThisProcess} of ${feed.connectionBudget} (this process)`}
                  hint="Dhan allows this many concurrent connections per user. This process can only account for its own — a second instance on the same credentials takes another slot invisibly."
                />
                <Field
                  label="Reconnects"
                  mono
                  value={feed.reconnects}
                  color={feed.reconnects > 0 ? theme.palette.warning.main : undefined}
                  hint="A rising count with a healthy-looking state means the connection is flapping, which a point-in-time status hides."
                />
                <Field
                  label="Reconnect backoff"
                  mono
                  value={`${feed.reconnectBackoffInitialSeconds}s → ${feed.reconnectBackoffMaxSeconds}s`}
                />
              </>
            ) : null}
            <Divider sx={{ my: 0.5 }} />
            <Field
              label="Subscribed"
              mono
              value={`${formatQty(feed.subscribed)} of ${formatQty(feed.maxInstrumentsPerConnection)}`}
              hint="Instruments on this connection, against the per-connection ceiling."
            />
            <Field label="Subscribe batch size" mono value={feed.maxInstrumentsPerSubscribe} />
            <Field label="Frames received" mono value={formatCompact(feed.framesReceived)} />
            <Field label="Packets applied" mono value={formatCompact(feed.packetsApplied)} />
            {feed.error ? (
              <Alert severity="error" sx={{ mt: 1 }}>
                {feed.error}
              </Alert>
            ) : null}
            <Typography variant="caption" color="text.secondary" sx={{ pt: 0.5 }}>
              These counters belong to the feed client, which a settings save replaces
              outright — so they reset on a save. The broadcaster and greeks counters
              elsewhere on this page do not.
            </Typography>
          </SectionCard>
        ) : null}

        {/* --- credentials -------------------------------------------------- */}
        {credentials ? (
          <SectionCard
            title="Credentials"
            subtitle="Market data only. The token is never returned to the browser."
          >
            {credentials.storedSettings?.appliedToThisProcess === false ? (
              <Alert severity="warning" sx={{ mb: 1 }}>
                <AlertTitle>Saved settings are not in effect</AlertTitle>
                {credentials.storedSettings.note} Differing:{' '}
                {credentials.storedSettings.keysDiffering.join(', ')}
              </Alert>
            ) : null}
            <Field
              label="Client id"
              mono
              value={credentials.clientId ?? 'not configured'}
              hint="The effective value — what the feed would actually use — not necessarily what is saved on the Settings page."
            />
            <Field
              label="Access token"
              mono
              value={credentials.token?.masked ?? 'not configured'}
              hint="A mask, not the token. The server returns the first and last four characters and the length."
            />
            {credentials.token?.present ? (
              credentials.token.isJwt && credentials.token.expiresAt ? (
                <>
                  <Field
                    label="Expires"
                    mono
                    color={
                      credentials.token.expired
                        ? theme.market.down
                        : credentials.token.secondsRemaining < 2 * 3600
                          ? theme.palette.warning.main
                          : theme.market.up
                    }
                    value={
                      credentials.token.expired
                        ? 'expired'
                        : formatCountdown(credentials.token.secondsRemaining)
                    }
                  />
                  <Field
                    label="Expires at"
                    value={new Date(credentials.token.expiresAt).toLocaleString('en-IN', {
                      hour12: false,
                    })}
                  />
                </>
              ) : (
                <Typography variant="caption" color="text.secondary">
                  Stored, but not a JWT — its expiry cannot be read locally.
                </Typography>
              )
            ) : null}
            <Divider sx={{ my: 0.5 }} />
            <Field label="Feed mode" value={credentials.syntheticFeed ? 'synthetic' : 'live Dhan'} />
            <Field
              label="Encryption key"
              value={credentials.encryptionConfigured ? 'configured' : 'NOT configured'}
              color={credentials.encryptionConfigured ? undefined : theme.market.down}
              hint="Without a stable key the app refuses to store a token at rest rather than encrypting it under a per-process key it could never read back."
            />
          </SectionCard>
        ) : null}
      </Box>

      {/* --- background tasks ---------------------------------------------- */}
      {tasks ? (
        <Card>
          <CardContent>
            <Stack direction="row" justifyContent="space-between" alignItems="baseline" spacing={2}>
              <Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                  Background tasks
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {tasks.runningCount} of {tasks.expectedCount} expected tasks running, plus{' '}
                  {tasks.transientCount} transient request/socket task
                  {tasks.transientCount === 1 ? '' : 's'}. This is the honest answer to “threads”:
                  there is no thread pool here.
                </Typography>
              </Box>
              <StateChip state={tasks.healthy ? 'running' : 'missing'} />
            </Stack>
            <TableContainer sx={{ mt: 1.5 }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Task</TableCell>
                    <TableCell>State</TableCell>
                    <TableCell align="right">Interval</TableCell>
                    <TableCell align="right">Progress</TableCell>
                    <TableCell>What it does</TableCell>
                    <TableCell>Last error</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {sortedTasks.map((task) => (
                    <TableRow
                      key={task.name}
                      sx={{
                        bgcolor:
                          task.state === 'missing'
                            ? theme.market.downSoft
                            : task.state === 'unexpected'
                              ? theme.market.itm
                              : undefined,
                      }}
                    >
                      <TableCell sx={{ fontFamily: 'monospace' }}>{task.name}</TableCell>
                      <TableCell>
                        <StateChip state={task.state} />
                      </TableCell>
                      <TableCell align="right" className="numeric">
                        {task.intervalMs ? `${task.intervalMs} ms` : '—'}
                      </TableCell>
                      <TableCell align="right" className="numeric">
                        {task.runs === null || task.runs === undefined
                          ? '—'
                          : `${formatCompact(task.runs)} ${task.runsLabel ?? ''}`}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {task.description}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        {task.lastError ? (
                          <Typography variant="caption" sx={{ color: theme.market.down }}>
                            {task.lastError}
                          </Typography>
                        ) : (
                          <Typography variant="caption" color="text.disabled">
                            none
                          </Typography>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      ) : null}

      {/* --- browser sockets ------------------------------------------------ */}
      {sockets ? (
        <Card>
          <CardContent>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              Browser sockets
            </Typography>
            <Typography variant="caption" color="text.secondary">
              One per open tab, each with its own {sockets.maxClientQueue}-message queue flushed
              every {sockets.intervalMs} ms. A tab that cannot drain its queue has the backlog
              discarded and is resynchronised — correct, but invisible until now.
            </Typography>
            <Stack direction="row" spacing={3} sx={{ mt: 1.5, mb: 1 }} flexWrap="wrap">
              <Field label="Connected" mono value={sockets.clientCount} />
              <Field label="Flushes" mono value={formatCompact(sockets.broadcasts)} />
              <Field label="Rows sent" mono value={formatCompact(sockets.rowsSent)} />
              <Field
                label="Resyncs (live tabs)"
                mono
                value={sockets.droppedTotal}
                color={sockets.droppedTotal > 0 ? theme.palette.warning.main : undefined}
              />
              <Field
                label="Rejected handshakes"
                mono
                value={sockets.rejectedHandshakes}
                color={sockets.rejectedHandshakes > 0 ? theme.palette.warning.main : undefined}
                hint="Sockets refused at the handshake: bad cookie, expired session, or a password change owed. Looks to a user like the page simply not updating."
              />
            </Stack>
            {sockets.clients?.length ? (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Client</TableCell>
                      <TableCell>User</TableCell>
                      <TableCell align="right">Connected for</TableCell>
                      <TableCell>Subscribes to</TableCell>
                      <TableCell align="right">Depth</TableCell>
                      <TableCell align="right">Queued</TableCell>
                      <TableCell align="right">Resyncs</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {sockets.clients.map((client) => (
                      <TableRow
                        key={client.clientId}
                        sx={{ bgcolor: client.dropped > 0 ? theme.market.downSoft : undefined }}
                      >
                        <TableCell sx={{ fontFamily: 'monospace' }}>{client.clientId}</TableCell>
                        <TableCell>{client.user ?? '—'}</TableCell>
                        <TableCell align="right" className="numeric">
                          {formatAge(client.connectedForMs)}
                        </TableCell>
                        <TableCell>
                          {client.securityIds === null ? (
                            <Tooltip title="This tab is taking every subscribed instrument, depth included unless it turned it off.">
                              <Chip size="small" label="everything" sx={{ height: 20 }} />
                            </Tooltip>
                          ) : (
                            <Typography variant="caption" className="numeric">
                              {client.securityIds.length} id
                              {client.securityIds.length === 1 ? '' : 's'}:{' '}
                              {client.securityIds.join(', ')}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell align="right">{client.includeDepth ? 'yes' : 'no'}</TableCell>
                        <TableCell align="right" className="numeric">
                          {client.queued}
                        </TableCell>
                        <TableCell
                          align="right"
                          className="numeric"
                          sx={{ color: client.dropped > 0 ? theme.market.down : undefined }}
                        >
                          {client.dropped}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No browser tabs are connected to the feed right now.
              </Typography>
            )}
          </CardContent>
        </Card>
      ) : null}

      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)' },
        }}
      >
        {/* --- Dhan REST ---------------------------------------------------- */}
        {dhan ? (
          <SectionCard
            title="Dhan REST usage"
            subtitle="Plain HTTPS, not the WebSocket. These fail independently of the feed — a connected feed says nothing about whether greeks are arriving."
          >
            {['optionChain', 'charts'].map((key) => {
              const client = dhan[key];
              const label = key === 'optionChain' ? 'Option chain' : 'Charts';
              return (
                <Box key={key} sx={{ pb: 1 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {client.purpose}
                  </Typography>
                  <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                    <Field label="Requests" mono value={formatCompact(client.requests)} />
                    <Field
                      label="Errors"
                      mono
                      value={client.errors}
                      color={client.errors > 0 ? theme.market.down : undefined}
                    />
                    <Field
                      label="Rate limited"
                      mono
                      value={client.rateLimited}
                      color={client.rateLimited > 0 ? theme.palette.warning.main : undefined}
                    />
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                    {client.limit}
                    {client.limitIsOurs ? '' : ''}
                  </Typography>
                  <Typography variant="caption" color="text.disabled" sx={{ display: 'block' }}>
                    Counted since {client.countersSince}.
                  </Typography>
                  {key === 'optionChain' ? <Divider sx={{ mt: 1 }} /> : null}
                </Box>
              );
            })}
            <Typography variant="caption" color="text.secondary">
              This application's own API has no rate limiting and no request metrics — the access
              log is the only record of it.
            </Typography>
          </SectionCard>
        ) : null}

        {/* --- data freshness ------------------------------------------------ */}
        {freshness ? (
          <SectionCard title="Data freshness" subtitle="How old is what this is trading on">
            <Field
              label="Instruments"
              mono
              value={
                freshness.instruments
                  ? formatQty(freshness.instruments.instrumentCount)
                  : 'could not read'
              }
            />
            <Field
              label="Master refreshed"
              value={
                freshness.instruments?.lastRefreshedAt
                  ? new Date(freshness.instruments.lastRefreshedAt).toLocaleString('en-IN', {
                      hour12: false,
                    })
                  : 'never'
              }
            />
            <Field
              label="Master cache"
              value={freshness.instruments?.cacheFresh ? 'fresh' : 'stale — refresh it'}
              color={freshness.instruments?.cacheFresh ? undefined : theme.palette.warning.main}
            />
            <Divider sx={{ my: 0.5 }} />
            <Field label="Near-month future" mono value={freshness.nearFutureSecurityId} />
            <Field
              label="Subscribed expiries"
              value={freshness.subscribedExpiries?.join(', ') || 'none'}
            />
            <Field label="Strike window" mono value={`ATM ± ${freshness.strikeWindow}`} />
            <Field label="Window centre" mono value={freshness.windowCentre} />
            <Field label="Last resync" value={formatTime(freshness.lastResyncMs)} />
            <Divider sx={{ my: 0.5 }} />
            <Field
              label="Greeks last poll"
              mono
              value={formatAge(workers?.greeksPoller?.lastPollAgeMs)}
            />
            <Field label="Greeks legs merged" mono value={formatCompact(workers?.greeksPoller?.legsMerged)} />
            <Divider sx={{ my: 0.5 }} />
            <Field label="Charge rate card" mono value={freshness.chargeRatesVersion} />
            <Typography variant="caption" color="text.secondary">
              {freshness.chargeRatesNote}
            </Typography>
          </SectionCard>
        ) : null}
      </Box>

      {/* --- recent problems ------------------------------------------------ */}
      {problems ? (
        <Card>
          <CardContent>
            <Stack direction="row" justifyContent="space-between" alignItems="baseline" spacing={2}>
              <Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                  Recent warnings and errors
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {problems.note}
                </Typography>
              </Box>
              <Stack direction="row" spacing={1}>
                {Object.entries(problems.counts ?? {}).map(([level, count]) => (
                  <Chip
                    key={level}
                    size="small"
                    label={`${count} ${level}`}
                    variant="outlined"
                    sx={{
                      height: 22,
                      borderColor:
                        level === 'ERROR' || level === 'CRITICAL'
                          ? theme.market.down
                          : theme.palette.warning.main,
                    }}
                  />
                ))}
              </Stack>
            </Stack>

            {problems.entries?.length ? (
              <TableContainer sx={{ mt: 1.5, maxHeight: 360 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ width: 96 }}>Time</TableCell>
                      <TableCell sx={{ width: 80 }}>Level</TableCell>
                      <TableCell sx={{ width: 200 }}>Logger</TableCell>
                      <TableCell>Message</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {problems.entries.map((entry, index) => (
                      <TableRow key={`${entry.timestampMs}-${index}`}>
                        <TableCell className="numeric">{formatTime(entry.timestampMs)}</TableCell>
                        <TableCell
                          sx={{
                            color:
                              entry.level === 'WARNING' ? theme.palette.warning.main : theme.market.down,
                          }}
                        >
                          {entry.level}
                        </TableCell>
                        <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>
                          {entry.logger}
                        </TableCell>
                        <TableCell>
                          <Typography
                            variant="caption"
                            component="pre"
                            sx={{ m: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                          >
                            {entry.message}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            ) : (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
                Nothing has been logged at WARNING or above since this process started.
              </Typography>
            )}
          </CardContent>
        </Card>
      ) : null}
        </>
      )}
    </Stack>
  );
}
