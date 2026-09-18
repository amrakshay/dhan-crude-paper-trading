import { useCallback, useMemo, useState } from 'react';
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
 * The Configuration tab: everything about ONE strategy an operator may change,
 * edited as a FORM. Nothing takes effect until Save.
 *
 * Why a form rather than live switches. These settings change what software
 * does with money while nobody is watching, and several of them only make
 * sense together — turning the regime gate back on and enabling the off-gate
 * variant are contradictory in one order and fine in the other. Applying each
 * flick of a switch immediately makes a half-finished intention into a live
 * configuration, and it means an operator cannot look at what they are about to
 * do before it is already true. So the controls edit a DRAFT, one Save applies
 * it, and one dialog shows every change with the server's warnings before any
 * of it happens.
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
 *
 * Every control is admin-only and disabled otherwise; the server refuses
 * regardless of what this page shows (§5).
 */

const POLICY_REGIME_ENFORCE = 'regime.enforce';
const POLICY_OFF_GATE = 'off_gate.enabled';

/**
 * The order changes are applied in, which is not cosmetic.
 *
 * The server refuses "off-gate variant enabled" together with "regime gate not
 * enforced" — two overrides of the same thing. A draft that moves BOTH is legal
 * at the end and can be illegal in the middle, so the restrictive move goes
 * first in each direction: switch the gate's enforcement ON before enabling the
 * variant, and switch the variant OFF before relaxing the gate. Without this an
 * otherwise valid Save fails halfway with a message about a state the operator
 * never asked for.
 */
function orderPolicyChanges(changes) {
  const restrictiveFirst = (change) => {
    if (change.key === POLICY_REGIME_ENFORCE) return change.next ? 0 : 1;
    if (change.key === POLICY_OFF_GATE) return change.next ? 1 : 0;
    return 0;
  };
  return [...changes].sort((a, b) => restrictiveFirst(a) - restrictiveFirst(b));
}

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

/** A pending edit, in the words the operator will read on the dialog. */
function describeChange(change) {
  if (change.kind === 'policy') {
    return `${change.label}: ${change.fromLabel} → ${change.toLabel}`;
  }
  return `${change.label}: ${change.from} → ${change.to} IST`;
}

export default function SwingConfiguration({ status, isAdmin, onChanged }) {
  const [draft, setDraft] = useState({});
  const [confirming, setConfirming] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const strategyKey = status?.strategyKey;
  const policies = useMemo(() => status?.policies ?? [], [status]);
  const settings = useMemo(() => status?.settings ?? [], [status]);

  /** What the server says right now, flattened to one map. */
  const serverState = useMemo(() => {
    const state = {};
    policies.forEach((policy) => {
      state[`policy:${policy.key}`] = policy.enforced;
    });
    settings.forEach((setting) => {
      state[`setting:${setting.key}`] = setting.value;
    });
    return state;
  }, [policies, settings]);

  /**
   * The draft holds ONLY the fields the operator has actually changed.
   *
   * That one decision removes the whole class of bugs this form would otherwise
   * have. The page polls every 10 s: a draft seeded with every field would
   * either be overwritten on each poll (deleting a half-typed time under the
   * operator's hands) or would stop tracking the server entirely (so a change
   * made elsewhere would show up as an edit of theirs, offering to save a
   * revert nobody asked for). Holding only the edits means an untouched field
   * follows the server for free, and an edited one is left alone.
   *
   * Setting a field back to the server's own value REMOVES it from the draft,
   * so it stops counting as a change and starts tracking again.
   */
  const valueOf = (key) => (key in draft ? draft[key] : serverState[key]);
  const setValue = (key, value) =>
    setDraft((current) => {
      const next = { ...current };
      if (value === serverState[key]) delete next[key];
      else next[key] = value;
      return next;
    });

  const timeLooksValid = (value) => /^([01]\d|2[0-3]):[0-5]\d$/.test(value ?? '');

  /** Every field the operator has actually moved. */
  const changes = useMemo(() => {
    const found = [];
    policies.forEach((policy) => {
      const key = `policy:${policy.key}`;
      if (!(key in draft)) return;
      const next = draft[key];
      // The server may have caught up with an edit -- someone else made the
      // same change while this form was open. Then it is no longer a change.
      if (next === policy.enforced) return;
      found.push({
        kind: 'policy',
        key: policy.key,
        label: policy.label,
        next,
        fromLabel: policy.enforced ? policy.onLabel : policy.offLabel,
        toLabel: next ? policy.onLabel : policy.offLabel,
      });
    });
    settings.forEach((setting) => {
      const key = `setting:${setting.key}`;
      if (!(key in draft)) return;
      const next = draft[key];
      if (next === setting.value) return;
      found.push({
        kind: 'setting',
        key: setting.key,
        label: setting.label,
        from: setting.value,
        to: next,
        // A stored override identical to the strategy's own configured value
        // is noise, so going back to the default CLEARS the row rather than
        // storing the same string again.
        payload: next === setting.default ? null : next,
        valid: timeLooksValid(next),
      });
    });
    return found;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, policies, settings, serverState]);

  /**
   * The contradiction, caught in the DRAFT so Save is never offered for it.
   *
   * The server refuses this pair too, and that refusal is the authority — this
   * is so the operator sees it while they are still deciding rather than after
   * half the change has been applied.
   */
  const contradiction = useMemo(() => {
    const enforceRegime = valueOf(`policy:${POLICY_REGIME_ENFORCE}`);
    const offGate = valueOf(`policy:${POLICY_OFF_GATE}`);
    if (offGate === true && enforceRegime === false) {
      return (
        'The off-gate variant and a relaxed regime gate are two different ' +
        'answers to the same question — what to do while the index is below ' +
        'its 200-session SMA. Choose one: switch the variant off, or switch ' +
        'the regime gate back on.'
      );
    }
    return null;
  }, [draft, serverState]); // eslint-disable-line react-hooks/exhaustive-deps

  const badTimes = changes.filter((one) => one.kind === 'setting' && !one.valid);
  const canSave =
    isAdmin && changes.length > 0 && !contradiction && badTimes.length === 0 && !saving;

  const discard = () => {
    setDraft({});
    setError(null);
    setNotice(null);
  };

  /** Ask the server what each pending change would do — and whether it is refused. */
  const openConfirm = async () => {
    setError(null);
    setConfirming({ loading: true, items: [] });
    try {
      const items = await Promise.all(
        changes.map(async (change) => {
          try {
            const response =
              change.kind === 'policy'
                ? await strategiesApi.policyWarnings(strategyKey, change.key, change.next)
                : await strategiesApi.settingWarnings(strategyKey, change.key, change.to);
            return {
              change,
              warnings: response.warnings ?? [],
              refusal: response.refusal ?? null,
            };
          } catch {
            return { change, warnings: [], refusal: null };
          }
        }),
      );
      setConfirming({ loading: false, items });
    } catch (confirmError) {
      setConfirming(null);
      setError(confirmError.message);
    }
  };

  const save = useCallback(async () => {
    setSaving(true);
    setError(null);
    const applied = [];
    const failed = [];

    const policyChanges = orderPolicyChanges(
      changes.filter((one) => one.kind === 'policy'),
    );
    const settingChanges = changes.filter((one) => one.kind === 'setting');

    for (const change of [...policyChanges, ...settingChanges]) {
      try {
        if (change.kind === 'policy') {
          await strategiesApi.setStrategyPolicy(strategyKey, change.key, change.next);
        } else {
          await strategiesApi.setStrategySetting(strategyKey, change.key, change.payload);
        }
        applied.push(change);
      } catch (saveError) {
        failed.push({ change, message: saveError.message });
      }
    }

    // Say exactly what landed and what did not. A half-applied save reported as
    // one failure would leave an operator guessing which half.
    if (failed.length) {
      setError(
        `${applied.length} change(s) applied. ${failed
          .map((one) => `${one.change.label} was NOT applied: ${one.message}`)
          .join(' ')}`,
      );
    } else {
      setNotice(
        `${applied.length} change(s) saved. Rule changes apply at the next ` +
          `decision and do not touch open positions; timing changes apply to ` +
          `the next run.`,
      );
    }

    // Whatever happened, the page must show the truth: drop the draft and
    // re-read rather than assuming the save did what was asked.
    setDraft({});
    setConfirming(null);
    setSaving(false);
    await onChanged?.();
  }, [changes, strategyKey, onChanged]);

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

      {contradiction ? (
        <Alert severity="error" icon={<WarningAmberIcon />}>
          {contradiction}
        </Alert>
      ) : null}

      <Box>
        <Typography variant="overline" color="text.secondary">
          Timings
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          When the strategy wakes up, in IST. Moving these changes when it acts,
          not what it decides. A saved change applies to the next run — a job
          already running keeps the schedule it started under, and one whose
          time has already passed today does not fire retrospectively.
        </Typography>
        <Paper variant="outlined" sx={{ mt: 1 }}>
          <Stack divider={<Divider />}>
            {settings.map((setting) => {
              const key = `setting:${setting.key}`;
              const value = valueOf(key) ?? '';
              const edited = value !== setting.value;
              const valid = timeLooksValid(value);
              return (
                <Row
                  key={setting.key}
                  title={setting.label}
                  subtitle={setting.description}
                  chips={
                    <Stack direction="row" spacing={0.5}>
                      {setting.overridden ? (
                        <Chip
                          size="small"
                          label={`configured: ${setting.default}`}
                          sx={{ bgcolor: 'action.selected', color: 'text.secondary' }}
                        />
                      ) : null}
                      {edited ? (
                        <Chip
                          size="small"
                          label="unsaved"
                          sx={{ bgcolor: 'warning.main', color: 'warning.contrastText' }}
                        />
                      ) : null}
                    </Stack>
                  }
                  control={
                    <TextField
                      size="small"
                      value={value}
                      disabled={!isAdmin || saving}
                      onChange={(event) => setValue(key, event.target.value)}
                      placeholder="HH:MM"
                      inputProps={{ style: { width: 72, textAlign: 'center' } }}
                      error={!valid}
                      helperText={valid ? 'IST' : '24-hour HH:MM'}
                    />
                  }
                >
                  <Typography variant="caption" color="text.disabled" display="block">
                    Allowed: {setting.allowed}
                  </Typography>
                </Row>
              );
            })}
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
            {policies.map((policy) => {
              const key = `policy:${policy.key}`;
              const value = Boolean(valueOf(key));
              const edited = value !== policy.enforced;
              return (
                <Row
                  key={policy.key}
                  title={policy.label}
                  subtitle={policy.description}
                  chips={
                    <Stack direction="row" spacing={0.5}>
                      {policy.enforced !== policy.default ? (
                        <Chip
                          size="small"
                          label={`default: ${
                            policy.default
                              ? policy.onLabel.toLowerCase()
                              : policy.offLabel.toLowerCase()
                          }`}
                          sx={{ bgcolor: 'action.selected', color: 'text.secondary' }}
                        />
                      ) : null}
                      {edited ? (
                        <Chip
                          size="small"
                          label="unsaved"
                          sx={{ bgcolor: 'warning.main', color: 'warning.contrastText' }}
                        />
                      ) : null}
                    </Stack>
                  }
                  control={
                    <Stack alignItems="center">
                      <Typography
                        variant="caption"
                        sx={{ fontWeight: 600 }}
                        color={value ? 'success.main' : 'warning.main'}
                      >
                        {value ? policy.onLabel : policy.offLabel}
                      </Typography>
                      <Switch
                        color={value ? 'primary' : 'warning'}
                        checked={value}
                        disabled={!isAdmin || saving}
                        onChange={(event) => setValue(key, event.target.checked)}
                      />
                    </Stack>
                  }
                />
              );
            })}
          </Stack>
        </Paper>
      </Box>

      {/* The only thing that makes any of the above real. Sticky, because the
          form is longer than a screen and a Save button nobody can see is a
          form that silently discards work. */}
      <Paper
        variant="outlined"
        sx={{
          position: 'sticky',
          bottom: 16,
          p: 2,
          zIndex: 2,
          borderColor: changes.length ? 'warning.main' : 'divider',
        }}
      >
        <Stack
          direction="row"
          spacing={2}
          alignItems="center"
          justifyContent="space-between"
          flexWrap="wrap"
          useFlexGap
        >
          <Typography variant="body2" color={changes.length ? 'warning.main' : 'text.secondary'}>
            {changes.length === 0
              ? 'No unsaved changes. Nothing on this page has taken effect that is not shown above.'
              : `${changes.length} unsaved change${changes.length === 1 ? '' : 's'} — nothing takes effect until you save.`}
          </Typography>
          <Stack direction="row" spacing={1}>
            <Button onClick={discard} disabled={!changes.length || saving}>
              Discard
            </Button>
            <Button
              variant="contained"
              color="warning"
              onClick={openConfirm}
              disabled={!canSave}
            >
              {saving ? 'Saving…' : 'Review and save'}
            </Button>
          </Stack>
        </Stack>
        {badTimes.length ? (
          <Typography variant="caption" color="error.main">
            {badTimes.map((one) => one.label).join(', ')}: use 24-hour HH:MM.
          </Typography>
        ) : null}
      </Paper>

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

      {confirming ? (
        <Dialog open onClose={() => setConfirming(null)} maxWidth="sm" fullWidth>
          <DialogTitle>
            Save {changes.length} change{changes.length === 1 ? '' : 's'}?
          </DialogTitle>
          <DialogContent>
            {confirming.loading ? (
              <Stack direction="row" spacing={1.5} alignItems="center" sx={{ py: 2 }}>
                <CircularProgress size={16} />
                <Typography variant="body2" color="text.secondary">
                  Checking what each change would do…
                </Typography>
              </Stack>
            ) : (
              <DialogContentText component="div">
                <Stack spacing={2}>
                  {confirming.items.map((item) => (
                    <Box key={`${item.change.kind}:${item.change.key}`}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {describeChange(item.change)}
                      </Typography>
                      {item.refusal ? (
                        <Alert
                          severity="error"
                          icon={<WarningAmberIcon />}
                          sx={{ mt: 1 }}
                        >
                          {item.refusal}
                        </Alert>
                      ) : null}
                      <Stack spacing={1} sx={{ mt: 1 }}>
                        {item.warnings.map((warning) => (
                          <Alert key={warning} severity="warning" icon={<WarningAmberIcon />}>
                            {warning}
                          </Alert>
                        ))}
                      </Stack>
                    </Box>
                  ))}
                </Stack>
              </DialogContentText>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setConfirming(null)}>Cancel</Button>
            <Button
              color="warning"
              variant="contained"
              disabled={
                confirming.loading ||
                saving ||
                confirming.items.some((item) => item.refusal)
              }
              onClick={save}
            >
              {confirming.items?.some((item) => item.refusal)
                ? 'Not allowed'
                : 'Save changes'}
            </Button>
          </DialogActions>
        </Dialog>
      ) : null}
    </Stack>
  );
}
