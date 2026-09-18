import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Paper,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { strategiesApi } from '../api/strategies';

/**
 * The Configuration tab: everything about this strategy an operator may change
 * at runtime, in one place, with what it costs stated in front of it.
 *
 * READ THIS BEFORE ADDING A CONTROL. Root CLAUDE.md §3a draws a line and this
 * page sits on one side of it:
 *
 *  - editable — whether a RULE is enforced (a boolean) and WHEN the machine
 *    wakes up (a time). Neither changes what the strategy is.
 *  - not editable, ever, from here — P1..P19. The momentum floor, the ATR
 *    multiple, the breadth ramp, the rank cut-off, every lookback, and the
 *    rebalance cadence (P18, measured both ways). Those live in the strategy's
 *    YAML so the file stays greppable against the specification's own table.
 *    The "How it works" tab renders them read-only, from that same file.
 *
 * Every control is admin-only and disabled otherwise; the server refuses
 * regardless of what this page shows (§5). Nothing here applies to a run
 * already in flight.
 */

function Row({ title, subtitle, chips, control, children }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      justifyContent="space-between"
      gap={2}
      sx={{ px: 2, py: 1.75 }}
    >
      <Box sx={{ flex: 1 }}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {title}
          </Typography>
          {chips}
        </Stack>
        <Typography variant="caption" color="text.secondary" display="block">
          {subtitle}
        </Typography>
        {children}
      </Box>
      {control}
    </Stack>
  );
}

function ConfirmDialog({
  title,
  body,
  warnings,
  refusal,
  confirmLabel,
  tone,
  onCancel,
  onConfirm,
}) {
  return (
    <Dialog open onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <DialogContentText component="div">
          {body ? (
            <Typography variant="body2" sx={{ mb: 2 }}>
              {body}
            </Typography>
          ) : null}
          {/* The server's own refusal, shown BEFORE the button rather than
              after it. A form that offers an edit the server will refuse is
              worse than one that does not (CLAUDE.md §5). */}
          {refusal ? (
            <Alert severity="error" icon={<WarningAmberIcon />} sx={{ mb: 2 }}>
              {refusal}
            </Alert>
          ) : null}
          <Stack spacing={1}>
            {warnings.map((warning) => (
              <Alert
                key={warning}
                severity={tone}
                icon={tone === 'warning' ? <WarningAmberIcon /> : <InfoOutlinedIcon />}
              >
                {warning}
              </Alert>
            ))}
          </Stack>
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          color="warning"
          variant="contained"
          disabled={Boolean(refusal)}
          onClick={onConfirm}
        >
          {refusal ? 'Not allowed' : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** One editable time, with its own draft state so typing does not fight the poll. */
function TimeSetting({ setting, disabled, onSave, onReset }) {
  const [draft, setDraft] = useState(setting.value);
  useEffect(() => {
    setDraft(setting.value);
  }, [setting.value]);

  const dirty = draft !== setting.value;
  const valid = /^([01]\d|2[0-3]):[0-5]\d$/.test(draft);

  return (
    <Row
      title={setting.label}
      subtitle={setting.description}
      chips={
        setting.overridden ? (
          <Chip
            size="small"
            label={`configured: ${setting.default}`}
            sx={{ bgcolor: 'warning.main', color: 'warning.contrastText' }}
          />
        ) : null
      }
      control={
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            size="small"
            value={draft}
            disabled={disabled}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="HH:MM"
            inputProps={{ style: { width: 72, textAlign: 'center' } }}
            error={!valid}
            helperText={valid ? 'IST' : '24-hour HH:MM'}
          />
          <Button
            size="small"
            variant="contained"
            color="warning"
            disabled={disabled || !dirty || !valid}
            onClick={() => onSave(setting, draft)}
          >
            Save
          </Button>
          {setting.overridden ? (
            <Button size="small" disabled={disabled} onClick={() => onReset(setting)}>
              Reset
            </Button>
          ) : null}
        </Stack>
      }
    >
      <Typography variant="caption" color="text.disabled" display="block">
        Allowed: {setting.allowed}
      </Typography>
    </Row>
  );
}

export default function SwingConfiguration({ status, isAdmin, onChanged }) {
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const strategyKey = status?.strategyKey;
  const policies = status?.policies ?? [];
  const settings = status?.settings ?? [];
  const disabled = !isAdmin || !strategyKey;

  const requestPolicy = async (policy, enforced) => {
    try {
      const response = await strategiesApi.policyWarnings(
        strategyKey,
        policy.key,
        enforced,
      );
      setPending({
        kind: 'policy',
        policy,
        enforced,
        warnings: response.warnings ?? [],
      });
    } catch {
      setPending({ kind: 'policy', policy, enforced, warnings: [] });
    }
  };

  const requestSetting = async (setting, value) => {
    try {
      const response = await strategiesApi.settingWarnings(
        strategyKey,
        setting.key,
        value,
      );
      setPending({
        kind: 'setting',
        setting,
        value,
        warnings: response.warnings ?? [],
        refusal: response.refusal ?? null,
      });
    } catch {
      setPending({ kind: 'setting', setting, value, warnings: [] });
    }
  };

  const applyPolicy = async (policy, enforced) => {
    try {
      await strategiesApi.setStrategyPolicy(strategyKey, policy.key, enforced);
      setNotice(
        `${policy.label}: ${enforced ? policy.onLabel : policy.offLabel}. It applies at the next decision; positions already open keep the policy they were opened under.`,
      );
      await onChanged?.();
    } catch (changeError) {
      setError(changeError.message);
    }
  };

  const applySetting = async (setting, value) => {
    try {
      await strategiesApi.setStrategySetting(strategyKey, setting.key, value);
      setNotice(
        value === null
          ? `${setting.label} is back to the configured ${setting.default} IST.`
          : `${setting.label} is now ${value} IST. It applies to the next run.`,
      );
      await onChanged?.();
    } catch (changeError) {
      // The server refuses a time that would break something — a bar refresh
      // during the session, or an order time outside it — and says why. That
      // sentence is the useful part, so it is shown rather than summarised.
      setError(changeError.message);
    }
  };

  if (!status) {
    return <Alert severity="info">Loading the strategy&apos;s configuration…</Alert>;
  }

  return (
    <Stack spacing={3}>
      {error ? (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      ) : null}
      {notice ? (
        <Alert severity="success" onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      ) : null}

      {!isAdmin ? (
        <Alert severity="info" icon={<InfoOutlinedIcon />}>
          Read-only: changing any of this needs an administrator. The values
          below are what the strategy is actually running on.
        </Alert>
      ) : null}

      <Box>
        <Typography variant="overline" color="text.secondary">
          Timings
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          When the strategy wakes up, in IST. Moving these changes when it acts,
          not what it decides. A change applies to the next run — a job already
          running keeps the schedule it started under, and one whose time has
          already passed today does not fire retrospectively.
        </Typography>
        <Paper variant="outlined" sx={{ mt: 1 }}>
          <Stack divider={<Divider />}>
            {settings.map((setting) => (
              <TimeSetting
                key={setting.key}
                setting={setting}
                disabled={disabled}
                onSave={requestSetting}
                onReset={(one) => applySetting(one, null)}
              />
            ))}
            {!settings.length ? (
              <Box sx={{ px: 2, py: 1.75 }}>
                <Typography variant="body2" color="text.disabled">
                  This module has no runtime timings.
                </Typography>
              </Box>
            ) : null}
          </Stack>
        </Paper>
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary">
          Rules — enforced or not
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          Whether this strategy OBEYS one of its own rules. The rules themselves
          — every threshold, lookback and multiple — live in its configuration
          file and are not editable here; the “How it works” tab shows them all.
        </Typography>
        {status.policyContradiction ? (
          <Alert severity="error" sx={{ mt: 1 }} icon={<WarningAmberIcon />}>
            {status.policyContradiction}
          </Alert>
        ) : null}
        <Paper variant="outlined" sx={{ mt: 1 }}>
          <Stack divider={<Divider />}>
            {policies.map((policy) => (
              <Row
                key={policy.key}
                title={policy.label}
                subtitle={policy.description}
                chips={
                  policy.enforced !== policy.default ? (
                    <Chip
                      size="small"
                      label={`default: ${
                        policy.default
                          ? policy.onLabel.toLowerCase()
                          : policy.offLabel.toLowerCase()
                      }`}
                      sx={{ bgcolor: 'warning.main', color: 'warning.contrastText' }}
                    />
                  ) : null
                }
                control={
                  <Stack alignItems="center">
                    <Typography
                      variant="caption"
                      sx={{ fontWeight: 600 }}
                      color={policy.enforced ? 'success.main' : 'warning.main'}
                    >
                      {policy.enforced ? policy.onLabel : policy.offLabel}
                    </Typography>
                    <Switch
                      color={policy.enforced ? 'primary' : 'warning'}
                      checked={policy.enforced}
                      disabled={disabled}
                      onChange={(event) => requestPolicy(policy, event.target.checked)}
                    />
                  </Stack>
                }
              />
            ))}
          </Stack>
        </Paper>
      </Box>

      <Box>
        <Typography variant="overline" color="text.secondary">
          Not editable here
        </Typography>
        <Alert severity="info" icon={<InfoOutlinedIcon />} sx={{ mt: 1 }}>
          <Typography variant="body2">
            Auto trade is on the Strategies &amp; Features page, beside the
            on/off switch — it is the one control that lets this software spend
            money on its own, and it lives with the rest of the strategy&apos;s
            running state.
          </Typography>
          <Typography variant="body2" sx={{ mt: 1 }}>
            The strategy&apos;s own numbers — the momentum floor, the ATR
            multiple, the breadth ramp, the rank cut-off, every lookback, and
            whether it rebalances daily or weekly — are deliberately not
            editable from any page. They were chosen in-sample against the
            specification and the configuration file has to stay checkable
            against it line by line. The “How it works” tab renders every one of
            them, read from that file.
          </Typography>
        </Alert>
      </Box>

      {pending?.kind === 'policy' ? (
        <ConfirmDialog
          title={`${pending.policy.label} → ${
            pending.enforced ? pending.policy.onLabel : pending.policy.offLabel
          }`}
          body={pending.policy.description}
          warnings={pending.warnings}
          tone={pending.enforced ? 'info' : 'warning'}
          confirmLabel={pending.enforced ? pending.policy.onLabel : pending.policy.offLabel}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const { policy, enforced } = pending;
            setPending(null);
            await applyPolicy(policy, enforced);
          }}
        />
      ) : null}

      {pending?.kind === 'setting' ? (
        <ConfirmDialog
          title={`${pending.setting.label} → ${pending.value} IST`}
          body={`Currently ${pending.setting.value} IST${
            pending.setting.overridden
              ? ` (the configured value is ${pending.setting.default})`
              : ''
          }.`}
          warnings={pending.warnings}
          refusal={pending.refusal}
          tone="warning"
          confirmLabel={`Move to ${pending.value}`}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const { setting, value } = pending;
            setPending(null);
            await applySetting(setting, value);
          }}
        />
      ) : null}
    </Stack>
  );
}
