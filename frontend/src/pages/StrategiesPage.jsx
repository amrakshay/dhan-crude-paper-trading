import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Grid,
  Paper,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { strategiesApi } from '../api/strategies';
import { useAuth } from '../auth/AuthContext';
import { formatCompact } from '../utils/format';

/**
 * Strategies & Features.
 *
 * Each card states what the strategy is currently COSTING — instruments on the
 * shared feed connection, expiries being polled, the greeks interval, open
 * positions, which portfolios run it. That is what makes the toggle an
 * informed decision rather than a switch in the dark, and every number comes
 * from state the server already keeps.
 *
 * A disabled card says what is NOT happening rather than going blank: "not
 * subscribed, no API calls, pages hidden" is information; an empty card is not.
 */

function CostLine({ label, value, hint }) {
  return (
    <Stack direction="row" spacing={0.5} alignItems="baseline">
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2" className="numeric" sx={{ fontWeight: 500 }}>
        {value}
      </Typography>
      {hint ? (
        <Tooltip title={hint}>
          <InfoOutlinedIcon sx={{ fontSize: 12, color: 'text.disabled' }} />
        </Tooltip>
      ) : null}
    </Stack>
  );
}

/**
 * Arming is the one control in this application that lets software spend money
 * without anyone clicking, so it gets its own confirmation with the server's
 * own warnings on it — the same treatment switching a strategy off gets, and
 * for the opposite reason.
 */
function ArmDialog({ strategy, warnings, onCancel, onConfirm }) {
  return (
    <Dialog open onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>Arm {strategy.label}?</DialogTitle>
      <DialogContent>
        <DialogContentText component="div">
          <Typography variant="body2" sx={{ mb: 2 }}>
            Enabled means it computes, decides and writes a decision record
            every session. ARMED means it may also submit orders on its own
            schedule. Paper money only — nothing reaches a broker — but the
            decisions, the sizes and the stops will be its own.
          </Typography>
          <Stack spacing={1}>
            {warnings.map((warning) => (
              <Alert key={warning} severity="warning" icon={<WarningAmberIcon />}>
                {warning}
              </Alert>
            ))}
          </Stack>
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button color="warning" variant="contained" onClick={onConfirm}>
          Arm it
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function DisableDialog({ strategy, warnings, onCancel, onConfirm }) {
  return (
    <Dialog open onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>Switch off {strategy.label}?</DialogTitle>
      <DialogContent>
        <DialogContentText component="div">
          <Typography variant="body2" sx={{ mb: 2 }}>
            It will stop subscribing instruments, stop polling greeks and stop
            fetching charts, and its live pages will disappear. Its orders,
            positions and P&amp;L stay exactly as they are.
          </Typography>
          {warnings.length > 0 ? (
            <Stack spacing={1}>
              {warnings.map((warning) => (
                <Alert key={warning} severity="warning" icon={<WarningAmberIcon />}>
                  {warning}
                </Alert>
              ))}
            </Stack>
          ) : (
            <Alert severity="info">Nothing is open under this strategy.</Alert>
          )}
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button color="warning" variant="contained" onClick={onConfirm}>
          Switch off
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function StrategiesPage() {
  const { isAdmin, refresh: refreshSession } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(null);
  const [arming, setArming] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await strategiesApi.list());
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const applyStrategy = async (key, enabled) => {
    try {
      const result = await strategiesApi.setStrategyEnabled(key, enabled);
      const effect = result.effect ?? {};
      setNotice(
        enabled
          ? `${key} is on. ${effect.subscribed ?? 0} instrument(s) subscribed.`
          : `${key} is off. ${effect.unsubscribed ?? 0} instrument(s) unsubscribed${
              effect.greeksPoller === 'stopped' ? ', greeks poller stopped' : ''
            }.`,
      );
      await load();
      // The sidebar is driven by the session's page list, which has just
      // changed: re-read it rather than leaving a dead nav item behind.
      await refreshSession?.();
    } catch (toggleError) {
      setError(toggleError.message);
    }
  };

  const requestToggle = async (strategy, enabled) => {
    if (enabled) {
      await applyStrategy(strategy.key, true);
      return;
    }
    try {
      const response = await strategiesApi.disableWarnings(strategy.key);
      setPending({ strategy, warnings: response.warnings ?? [] });
    } catch (warningError) {
      setPending({ strategy, warnings: [] });
    }
  };

  const applyArmed = async (key, armed) => {
    try {
      await strategiesApi.setStrategyArmed(key, armed);
      setNotice(
        armed
          ? `${key} is ARMED. It may now submit orders on its own schedule.`
          : `${key} is disarmed. It keeps deciding and recording; it places nothing. Open positions and their stops are untouched.`,
      );
      await load();
    } catch (armError) {
      setError(armError.message);
    }
  };

  const requestArm = async (strategy, armed) => {
    if (!armed) {
      await applyArmed(strategy.key, false);
      return;
    }
    try {
      const response = await strategiesApi.armWarnings(strategy.key);
      setArming({ strategy, warnings: response.warnings ?? [] });
    } catch (warningError) {
      setArming({ strategy, warnings: [] });
    }
  };

  const toggleCapability = async (key, enabled) => {
    try {
      await strategiesApi.setCapabilityEnabled(key, enabled);
      await load();
      await refreshSession?.();
    } catch (toggleError) {
      setError(toggleError.message);
    }
  };

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h2">Strategies &amp; Features</Typography>
        <Typography variant="body2" color="text.secondary">
          What is running, what it costs, and what stops when you switch it off.
          Credentials live on the Settings page.
        </Typography>
      </Box>

      {error ? <Alert severity="error">{error}</Alert> : null}
      {notice ? (
        <Alert severity="success" onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      ) : null}
      {loading && !data ? <CircularProgress size={22} /> : null}

      <Box>
        <Typography variant="overline" color="text.secondary">
          Strategy modules
        </Typography>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {(data?.strategies ?? []).map((strategy) => {
            const cost = strategy.cost ?? {};
            return (
              <Paper key={strategy.key} variant="outlined" sx={{ p: 2.5 }}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="flex-start"
                  gap={2}
                  flexWrap="wrap"
                >
                  <Box>
                    <Typography variant="h5" sx={{ fontWeight: 600 }}>
                      {strategy.label}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {strategy.symbol} · {strategy.exchangeSegment} ·{' '}
                      {strategy.marketOpen}–{strategy.marketClose}
                    </Typography>
                  </Box>
                  <Stack direction="row" spacing={2} alignItems="center">
                    {/* Two switches, shown as two. A module with no automation
                        block has nothing to arm and gets no control at all. */}
                    {strategy.automated ? (
                      <Stack alignItems="center">
                        <Typography
                          variant="caption"
                          color={strategy.armed ? 'warning.main' : 'text.secondary'}
                          sx={{ fontWeight: 600 }}
                        >
                          {strategy.armed ? 'ARMED' : 'NOT ARMED'}
                        </Typography>
                        <Switch
                          color="warning"
                          checked={strategy.armed}
                          disabled={!isAdmin}
                          onChange={(event) => requestArm(strategy, event.target.checked)}
                        />
                        <Typography variant="caption" color="text.disabled">
                          may place orders
                        </Typography>
                      </Stack>
                    ) : null}
                    <Stack alignItems="center">
                      <Typography
                        variant="caption"
                        color={strategy.enabled ? 'success.main' : 'text.disabled'}
                        sx={{ fontWeight: 600 }}
                      >
                        {strategy.enabled ? 'ON' : 'OFF'}
                      </Typography>
                      <Switch
                        checked={strategy.enabled}
                        disabled={!isAdmin}
                        onChange={(event) => requestToggle(strategy, event.target.checked)}
                      />
                      <Typography variant="caption" color="text.disabled">
                        {strategy.automated ? 'decides and records' : 'running'}
                      </Typography>
                    </Stack>
                  </Stack>
                </Stack>

                <Divider sx={{ my: 2 }} />

                {strategy.automated ? (
                  <Alert
                    severity={strategy.armed ? 'warning' : 'info'}
                    icon={strategy.armed ? <WarningAmberIcon /> : <InfoOutlinedIcon />}
                    sx={{ mb: 2 }}
                  >
                    {strategy.armed
                      ? 'Armed: this module places its own orders on its own schedule. Every one of them is paper money in this database.'
                      : 'Not armed: it computes, decides and writes a decision record every session, and places nothing. Watching it decide before arming it is the cheapest possible safeguard.'}
                  </Alert>
                ) : null}

                {strategy.enabled ? (
                  <Grid container spacing={2}>
                    <Grid item xs={12} sm={6} md={3}>
                      <CostLine
                        label="Instruments"
                        value={formatCompact(cost.instrumentsSubscribed ?? 0)}
                        hint="Contracts this strategy contributes to the one shared upstream connection."
                      />
                      <CostLine
                        label="Expiries"
                        value={(cost.expiriesSubscribed ?? []).join(', ') || '—'}
                      />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                      <CostLine
                        label="Greeks"
                        value={
                          cost.greeksIntervalSeconds
                            ? `every ${cost.greeksIntervalSeconds}s × ${cost.greeksExpiriesPolled}`
                            : 'off'
                        }
                        hint="An option-chain REST call against the Dhan token, per expiry."
                      />
                      <CostLine label="Rate card" value={strategy.chargesRateCard} />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                      <CostLine
                        label="Open positions"
                        value={cost.openPositions ?? 0}
                      />
                      <CostLine label="Resting orders" value={cost.restingOrders ?? 0} />
                      <CostLine
                        label="Open chart trades"
                        value={cost.openChartTrades ?? 0}
                      />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                      <CostLine
                        label="Portfolios"
                        value={(cost.portfolios ?? []).join(', ') || 'none'}
                      />
                      <CostLine
                        label="Margin"
                        value={`${(Number(strategy.marginPercentOfNotional) * 100).toFixed(
                          1,
                        )}% of notional (estimate)`}
                        hint="An APPROXIMATION for short positions. Real margin is SPAN + exposure and is not computed here."
                      />
                    </Grid>
                  </Grid>
                ) : (
                  <Stack spacing={0.5}>
                    <Typography variant="body2" color="text.secondary">
                      Not subscribed · no greeks polled · no chart requests · its
                      live pages are hidden
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {cost.openPositions ?? 0} open position
                      {(cost.openPositions ?? 0) === 1 ? '' : 's'} and{' '}
                      {cost.restingOrders ?? 0} resting order
                      {(cost.restingOrders ?? 0) === 1 ? '' : 's'} are frozen, not
                      cancelled. Its history is unchanged.
                    </Typography>
                  </Stack>
                )}

                <Stack direction="row" spacing={0.5} sx={{ mt: 2 }} flexWrap="wrap">
                  {strategy.capabilities.map((capability) => {
                    const active = strategy.activeCapabilities.includes(capability);
                    return (
                      <Chip
                        key={capability}
                        size="small"
                        label={capability}
                        variant={active ? 'filled' : 'outlined'}
                        // Colours set explicitly rather than through `color`:
                        // the theme's MuiChip override forces a background on
                        // every chip, so a `color="primary"` chip ends up with
                        // white text on the theme's grey and is unreadable.
                        sx={{
                          bgcolor: active ? 'primary.main' : 'transparent',
                          color: active ? 'primary.contrastText' : 'text.secondary',
                          borderColor: 'divider',
                        }}
                      />
                    );
                  })}
                </Stack>
              </Paper>
            );
          })}
        </Stack>
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary">
          Capabilities (apply to every strategy)
        </Typography>
        <Paper variant="outlined" sx={{ mt: 1 }}>
          <Stack divider={<Divider />}>
            {(data?.capabilities ?? []).map((capability) => (
              <Stack
                key={capability.key}
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                sx={{ px: 2, py: 1.25 }}
              >
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {capability.label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {capability.description}
                    {capability.page ? ` · grants ${capability.page}` : ''}
                    {capability.supportedBy.length === 0
                      ? ' · no strategy supports it'
                      : ''}
                  </Typography>
                </Box>
                <Switch
                  checked={capability.enabled}
                  disabled={!isAdmin}
                  onChange={(event) =>
                    toggleCapability(capability.key, event.target.checked)
                  }
                />
              </Stack>
            ))}
          </Stack>
        </Paper>
      </Box>

      {arming ? (
        <ArmDialog
          strategy={arming.strategy}
          warnings={arming.warnings}
          onCancel={() => setArming(null)}
          onConfirm={async () => {
            const key = arming.strategy.key;
            setArming(null);
            await applyArmed(key, true);
          }}
        />
      ) : null}

      {pending ? (
        <DisableDialog
          strategy={pending.strategy}
          warnings={pending.warnings}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const key = pending.strategy.key;
            setPending(null);
            await applyStrategy(key, false);
          }}
        />
      ) : null}
    </Stack>
  );
}
