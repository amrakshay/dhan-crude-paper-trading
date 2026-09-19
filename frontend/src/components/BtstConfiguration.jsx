import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Paper,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { btstApi } from '../api/btst';
import { strategiesApi } from '../api/strategies';

/**
 * Everything about THIS strategy an operator may change, in one place.
 *
 * Two rule switches and two clock times, and nothing else — because nothing
 * else is a runtime fact. B1–B16 live in the strategy's YAML and are editable
 * from no page, which is what keeps that file greppable against the
 * specification's own table (root `CLAUDE.md` section 3a). The tab says so
 * rather than leaving somebody hunting for a control that does not exist.
 *
 * **IT IS A FORM: nothing takes effect until Save.** The same rule the
 * rotation's Configuration tab follows, and for the same reasons — these
 * settings change what software does with money unattended, several only make
 * sense together, and an operator has to see what they are about to do before
 * it is already true.
 *
 * **The draft holds ONLY the fields actually changed.** With a poll running, a
 * draft seeded with every field either gets overwritten mid-edit or stops
 * tracking the server and offers to "save" a revert nobody asked for. Setting
 * a field back to the server's value removes it from the draft, so it stops
 * counting as a change and starts tracking again.
 *
 * **The dialog shows the server's warnings BEFORE the confirm button.** A form
 * that offers an edit the server will refuse is worse than one that does not,
 * so each pending change asks for its warnings and its refusal first.
 */
export default function BtstConfiguration({ strategyKey, isAdmin }) {
  const [payload, setPayload] = useState(null);
  const [draft, setDraft] = useState({});
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [warnings, setWarnings] = useState([]);
  const [saving, setSaving] = useState(false);
  const [results, setResults] = useState(null);

  const load = useCallback(async () => {
    try {
      setPayload(await btstApi.configuration(strategyKey));
      setError(null);
    } catch (caught) {
      setError(caught.message || 'Could not read the configuration');
    } finally {
      setLoading(false);
    }
  }, [strategyKey]);

  useEffect(() => {
    load();
  }, [load]);

  const policies = payload?.policies || [];
  const settings = payload?.settings || [];

  const pending = useMemo(() => {
    const changes = [];
    policies.forEach((one) => {
      const key = `policy:${one.key}`;
      if (key in draft && draft[key] !== one.enforced) {
        changes.push({ kind: 'policy', key: one.key, label: one.label, value: draft[key] });
      }
    });
    settings.forEach((one) => {
      const key = `setting:${one.key}`;
      if (key in draft && draft[key] !== one.value) {
        changes.push({ kind: 'setting', key: one.key, label: one.label, value: draft[key] });
      }
    });
    return changes;
  }, [draft, policies, settings]);

  /** Setting a field back to the server's value REMOVES it from the draft. */
  const edit = (key, value, serverValue) => {
    setDraft((current) => {
      const next = { ...current };
      if (value === serverValue) delete next[key];
      else next[key] = value;
      return next;
    });
  };

  const review = async () => {
    setConfirming(true);
    setWarnings([]);
    const collected = [];
    for (const change of pending) {
      try {
        const response =
          change.kind === 'policy'
            ? await strategiesApi.policyWarnings(strategyKey, change.key, change.value)
            : await strategiesApi.settingWarnings(strategyKey, change.key, change.value);
        collected.push({
          ...change,
          warnings: response.warnings || [],
          refusal: response.refusal || null,
        });
      } catch (caught) {
        collected.push({ ...change, warnings: [], refusal: caught.message });
      }
    }
    setWarnings(collected);
  };

  const save = async () => {
    setSaving(true);
    const applied = [];
    for (const change of pending) {
      try {
        if (change.kind === 'policy') {
          await strategiesApi.setStrategyPolicy(strategyKey, change.key, change.value);
        } else {
          await strategiesApi.setStrategySetting(strategyKey, change.key, change.value);
        }
        applied.push({ ...change, ok: true });
      } catch (caught) {
        // A HALF-APPLIED SAVE SAYS WHICH HALF. Assuming it did what was asked
        // is how an operator ends up believing a switch moved when it did not.
        applied.push({ ...change, ok: false, reason: caught.message });
      }
    }
    setResults(applied);
    setSaving(false);
    setConfirming(false);
    setDraft({});
    await load();
  };

  if (loading && !payload) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  const blocked = warnings.some((one) => one.refusal);

  return (
    <Stack spacing={2}>
      {error ? <Alert severity="error">{error}</Alert> : null}
      {results ? (
        <Alert severity={results.every((one) => one.ok) ? 'success' : 'warning'} onClose={() => setResults(null)}>
          {results.map((one) => (
            <div key={`${one.kind}:${one.key}`}>
              {one.label}: {one.ok ? 'applied' : `NOT applied — ${one.reason}`}
            </div>
          ))}
        </Alert>
      ) : null}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
          Rules
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Whether a rule is OBEYED. Never what the rule is.
        </Typography>
        <Stack spacing={1.5} sx={{ mt: 1.5 }}>
          {policies.map((one) => {
            const key = `policy:${one.key}`;
            const value = key in draft ? draft[key] : one.enforced;
            return (
              <Box key={one.key}>
                <FormControlLabel
                  disabled={!isAdmin}
                  control={
                    <Switch
                      checked={Boolean(value)}
                      onChange={(event) => edit(key, event.target.checked, one.enforced)}
                    />
                  }
                  label={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <span>{one.label}</span>
                      <Chip size="small" label={value ? one.onLabel : one.offLabel} />
                      {one.overridden ? (
                        <Chip size="small" variant="outlined" label="overridden" />
                      ) : null}
                    </Stack>
                  }
                />
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', ml: 6 }}>
                  {one.description} Shipped default: {one.default ? one.onLabel : one.offLabel}.
                </Typography>
              </Box>
            );
          })}
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ mb: 0.5 }}>
          Times
        </Typography>
        <Typography variant="caption" color="text.secondary">
          When the machine wakes up. Never what it decides.
        </Typography>
        <Stack spacing={2} sx={{ mt: 1.5 }}>
          {settings.map((one) => {
            const key = `setting:${one.key}`;
            const value = key in draft ? draft[key] : one.value;
            return (
              <Box key={one.key}>
                <TextField
                  size="small"
                  label={one.label}
                  value={value || ''}
                  disabled={!isAdmin}
                  onChange={(event) => edit(key, event.target.value, one.value)}
                  sx={{ width: 160 }}
                  inputProps={{ placeholder: 'HH:MM' }}
                />
                {one.overridden ? (
                  <Chip size="small" variant="outlined" label="overridden" sx={{ ml: 1 }} />
                ) : null}
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  {one.description}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  Allowed: {one.allowed} Shipped default: {one.default}.
                </Typography>
              </Box>
            );
          })}
        </Stack>
      </Paper>

      {/* What is NOT editable here, and why — sent by the server rather than
          composed by the page, so the sentence cannot drift from the rule. */}
      <Alert severity="info" icon={<InfoOutlinedIcon />}>
        {payload?.notEditable}
      </Alert>

      {isAdmin ? (
        <Box sx={{ position: 'sticky', bottom: 0, py: 1 }}>
          <Button
            variant="contained"
            disabled={pending.length === 0}
            onClick={review}
          >
            {pending.length === 0
              ? 'No changes'
              : `Save ${pending.length} change${pending.length === 1 ? '' : 's'}`}
          </Button>
        </Box>
      ) : null}

      <Dialog open={confirming} onClose={() => setConfirming(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Apply these changes?</DialogTitle>
        <DialogContent dividers>
          {warnings.length === 0 ? (
            <CircularProgress size={24} />
          ) : (
            <Stack spacing={2}>
              {warnings.map((one) => (
                <Box key={`${one.kind}:${one.key}`}>
                  <Typography variant="subtitle2">
                    {one.label} → {String(one.value)}
                  </Typography>
                  {one.refusal ? (
                    <Alert severity="error" sx={{ mt: 1 }}>
                      {one.refusal}
                    </Alert>
                  ) : null}
                  {one.warnings.map((text, index) => (
                    <Typography key={index} variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                      {text}
                    </Typography>
                  ))}
                  <Divider sx={{ mt: 1 }} />
                </Box>
              ))}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirming(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={blocked || saving || warnings.length === 0}
            onClick={save}
          >
            {blocked ? 'Refused by the server' : 'Apply'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
