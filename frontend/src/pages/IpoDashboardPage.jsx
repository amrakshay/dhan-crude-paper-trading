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
import IpoTable from '../components/IpoTable';
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
];

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
  const tab = TABS.some((item) => item.key === searchParams.get('tab'))
    ? searchParams.get('tab')
    : 'closing-today';

  const [payload, setPayload] = useState(null);
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(
    async ({ quiet = false } = {}) => {
      if (!quiet) setLoading(true);
      try {
        const [tabPayload, statusPayload] = await Promise.all([
          LOADERS[tab](),
          ipoApi.status(),
        ]);
        setPayload(tabPayload);
        setStatus(statusPayload);
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
          {TABS.map((item) => (
            <Tab key={item.key} value={item.key} label={item.label} />
          ))}
        </Tabs>

        <Box sx={{ p: 2 }}>
          {payload?.day ? (
            <Typography variant="body2" sx={{ mb: 1 }}>
              {tab === 'closing-today' ? 'Closing' : 'Closing next on'}{' '}
              <strong>{payload.day}</strong>
            </Typography>
          ) : null}

          {payload?.caveat ? (
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 2 }}>
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
