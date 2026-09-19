import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Link as MuiLink,
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { ipoApi, IPO_SOURCE_PAGE } from '../api/ipo';
import { connectionsApi } from '../api/connections';
import IpoTable from '../components/IpoTable';
import IpoStatus from '../components/IpoStatus';
import AlertsPanel from '../components/AlertsPanel';
import { useAuth } from '../auth/AuthContext';

/**
 * The IPO dashboard.
 *
 * **NOT A STRATEGY PAGE.** Nothing here places an order, touches a portfolio
 * or reads the feed. It shows what a public GMP source said, what this
 * application recorded, and which of the two steps on a closing IPO are still
 * outstanding. It is modelled on the shape of the Trade Notes page, not on
 * /swing or /btst.
 *
 * Three tabs, and each one says what it is NOT:
 *
 * - **Closing today** is the one with actions, because it is the only day
 *   those actions still change anything.
 * - **Closing next** resolves to the next weekday. There is no exchange
 *   holiday list in this codebase (`market_clock` makes the same choice), so
 *   the tab says a public holiday may put an IPO here a day early rather than
 *   pretending to a calendar the application does not have.
 * - **Listed** is FORWARD-ONLY and empty until the first IPO this application
 *   watched actually lists. The empty state says so, so it does not read as a
 *   bug.
 *
 * Polls at 30 s: nothing here moves at tick speed, the GMP behind it refreshes
 * hourly at best, and this page opens no socket.
 */

const POLL_MS = 30000;

const TABS = [
  { key: 'closing-today', label: 'Closing today' },
  { key: 'closing-next', label: 'Closing next' },
  { key: 'listed', label: 'Listed' },
  // ONE tab, not a Health tab and an Alerts tab. /swing and /btst have both
  // because they trade unattended; this sends a message. Admin-only, because
  // it serves machinery state and job detail lines -- the same exposure the
  // other two health surfaces are gated for.
  { key: 'status', label: 'Status', adminOnly: true },
];

// The alert category this feature's rules belong to. They are deliberately NOT
// strategy-scoped -- no strategy owns an IPO -- so the catalogue is asked by
// CATEGORY here rather than by strategy key. They stay on the System Health
// page too; this is a narrower view of the same list, not a second one.
const ALERT_CATEGORY = 'IPO';

const EMPTY_MESSAGES = {
  'closing-today': 'No mainboard IPO closes today. Nothing to do, and nothing will be sent.',
  'closing-next':
    'No mainboard IPO closes on the next day the exchange is open.',
  listed:
    'Nothing has listed yet. This tab is forward-only: it fills with IPOs this application watched close and then list, and nothing is backfilled from the source. It is empty on day one by design, not by fault.',
};

const LOADERS = {
  'closing-today': () => ipoApi.closingToday(),
  'closing-next': () => ipoApi.closingNext(),
  listed: () => ipoApi.listed(),
};

export default function IpoDashboardPage() {
  const { isAdmin } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  // Hiding the Status tab is presentation; the API refuses a ROLE_USER
  // independently. A hand-typed ?tab=status falls back to the first tab rather
  // than rendering a panel whose every request would 403.
  const visibleTabs = useMemo(
    () => TABS.filter((item) => !item.adminOnly || isAdmin),
    [isAdmin],
  );
  const requested = searchParams.get('tab');
  const tab = visibleTabs.some((item) => item.key === requested)
    ? requested
    : 'closing-today';

  const [payload, setPayload] = useState(null);
  const [status, setStatus] = useState(null);
  const [health, setHealth] = useState(null);
  const [catalogue, setCatalogue] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(
    async ({ quiet = false } = {}) => {
      if (!quiet) setLoading(true);
      try {
        const statusPayload = await ipoApi.status();
        setStatus(statusPayload);
        if (tab === 'status') {
          // The two halves of the Status tab. The catalogue comes from the
          // ONE alert catalogue, narrowed by category -- these rules are not
          // strategy-scoped, and they stay on the System Health page too.
          const [healthPayload, cataloguePayload] = await Promise.all([
            ipoApi.health(),
            connectionsApi.catalogue(undefined, ALERT_CATEGORY),
          ]);
          setHealth(healthPayload);
          setCatalogue(cataloguePayload);
        } else {
          setPayload(await LOADERS[tab]());
        }
        setError(null);
      } catch (exc) {
        setError(exc.message);
      } finally {
        setLoading(false);
      }
    },
    [tab],
  );

  useEffect(() => {
    load();
    const timer = setInterval(() => load({ quiet: true }), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const onAction = useCallback(
    async (ipo, action, value) => {
      setBusyId(ipo.id);
      try {
        await ipoApi.setAction(ipo.id, action, value);
        await load({ quiet: true });
      } catch (exc) {
        setError(exc.message);
      } finally {
        setBusyId(null);
      }
    },
    [load],
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await ipoApi.refresh();
      await load({ quiet: true });
    } catch (exc) {
      setError(exc.message);
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const outstanding = useMemo(
    () => (payload?.ipos ?? []).filter((ipo) => ipo.isOutstanding).length,
    [payload],
  );

  return (
    <Box>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        alignItems={{ sm: 'center' }}
        justifyContent="space-between"
        sx={{ mb: 2 }}
      >
        <Box>
          <Typography variant="h5">IPO Dashboard</Typography>
          <Typography variant="body2" color="text.secondary">
            Mainboard only. SME issues are filtered out at the source and never
            stored. GMP from{' '}
            <MuiLink href={IPO_SOURCE_PAGE} target="_blank" rel="noreferrer">
              investorgain.com
            </MuiLink>
            .
          </Typography>
        </Box>
        {isAdmin ? (
          <Button
            size="small"
            variant="outlined"
            startIcon={<RefreshIcon />}
            disabled={refreshing}
            onClick={onRefresh}
          >
            {refreshing ? 'Fetching…' : 'Refresh now'}
          </Button>
        ) : null}
      </Stack>

      {error ? (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      ) : null}

      {tab === 'closing-today' && outstanding > 0 ? (
        <Alert severity="warning" sx={{ mb: 2 }} icon={<InfoOutlinedIcon />}>
          {outstanding} IPO{outstanding > 1 ? 's' : ''} closing today still
          outstanding. Reminders go hourly from{' '}
          {status?.reminderFrom ?? '10:00'} to {status?.reminderTo ?? '17:00'}{' '}
          IST until each is marked Applied <strong>and</strong> Accepted, or
          Rejected. Applied alone does not stop them — an unaccepted mandate is
          a failed application.
        </Alert>
      ) : null}

      <Paper variant="outlined">
        <Tabs
          value={tab}
          onChange={(_event, value) => setSearchParams({ tab: value })}
          sx={{ borderBottom: 1, borderColor: 'divider' }}
        >
          {visibleTabs.map((item) => (
            <Tab key={item.key} value={item.key} label={item.label} />
          ))}
        </Tabs>

        <Box sx={{ p: 2 }}>
          {tab === 'status' ? (
            <Stack spacing={3}>
              <IpoStatus health={health} error={null} loading={loading} />
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  What this feature will tell you about
                </Typography>
                {/* The SAME AlertsPanel the system page and the two strategy
                    pages use. These two rules are not strategy-scoped, so the
                    catalogue is narrowed by category instead -- and they are
                    not withdrawn from the System Health page by appearing
                    here. */}
                <AlertsPanel catalogue={catalogue} error={null} loading={loading} />
              </Box>
            </Stack>
          ) : (
            <>
              {payload?.day ? (
                <Typography variant="body2" sx={{ mb: 1 }}>
                  {tab === 'closing-today' ? 'Closing' : 'Closing next on'}{' '}
                  <strong>{payload.day}</strong>
                </Typography>
              ) : null}

              {payload?.caveat ? (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  display="block"
                  sx={{ mb: 2 }}
                >
                  {payload.caveat}
                </Typography>
              ) : null}

              {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
                  <CircularProgress size={28} />
                </Box>
              ) : (
                <IpoTable
                  tab={tab}
                  ipos={payload?.ipos ?? []}
                  isAdmin={isAdmin}
                  busyId={busyId}
                  onAction={onAction}
                  emptyMessage={EMPTY_MESSAGES[tab]}
                />
              )}
            </>
          )}
        </Box>
      </Paper>

      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
        sx={{ mt: 2 }}
      >
        <Chip
          size="small"
          variant="outlined"
          color={status?.enabled && status?.running ? 'success' : 'warning'}
          label={
            status?.enabled
              ? status?.running
                ? 'Clock running'
                : 'Clock enabled, task not running'
              : 'Clock switched off (ipo.scheduler_enabled)'
          }
        />
        <Typography variant="caption" color="text.secondary">
          Daily refresh {status?.dailyRefreshAt ?? '13:00'} IST · reminders{' '}
          {status?.reminderFrom ?? '10:00'}–{status?.reminderTo ?? '17:00'} IST ·{' '}
          {status?.lastRefreshDetail
            ? `last refresh: ${status.lastRefreshDetail}`
            : 'no refresh recorded yet'}
        </Typography>
        {status?.lastError ? (
          <Typography variant="caption" color="warning.main">
            Last error: {status.lastError}
          </Typography>
        ) : null}
      </Stack>
    </Box>
  );
}
