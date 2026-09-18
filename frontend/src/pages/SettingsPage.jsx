import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  FormControlLabel,
  Link as MuiLink,
  Stack,
  Switch,
  Typography,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import SaveIcon from '@mui/icons-material/Save';
import { settingsApi } from '../api/settings';

/**
 * Settings: this application's own configuration.
 *
 * **The Dhan credentials moved to `/connections` on 2026-09-18.** Connections
 * owns anything that talks to a third party -- the client id, the token, the
 * validation, the auto-renew state.
 *
 * The synthetic-feed switch deliberately STAYED here. It is a mode of THIS
 * application rather than a credential, and it would sit oddly on a card
 * describing a connection to somebody else.
 *
 * The rule that spans both pages: **live mode is still refused without
 * credentials.** Turning the synthetic feed off with no Dhan credentials means
 * the feed errors out, and this application must never invent prices in their
 * place. The server enforces it; this page says where to go and fix it, because
 * finding out after clicking is a worse way to learn it
 * (frontend/CLAUDE.md §3).
 */

/** Where a value came from, so the .env fallback is never a mystery. */
function SourceChip({ source }) {
  const label = { database: 'saved here', env: 'from .env', default: 'not set' }[source] ?? source;
  return <Chip size="small" variant="outlined" label={label} sx={{ height: 20 }} />;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [syntheticFeed, setSyntheticFeed] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);

  const load = useCallback(async () => {
    try {
      const result = await settingsApi.get();
      setSettings(result);
      setSyntheticFeed(result.syntheticFeed);
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

  const credentialsPresent = Boolean(settings?.clientId && settings?.token?.present);
  // The server refuses this anyway. Disabling Save here is so the operator
  // sees the rule while they are still deciding, not after clicking.
  const wouldBeRefused = !syntheticFeed && !credentialsPresent;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const result = await settingsApi.save({ syntheticFeed });
      setSaved(result.message);
      setSettings(result.settings);
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Stack spacing={3} sx={{ maxWidth: 780 }}>
      <Box>
        <Typography variant="h2">Settings</Typography>
        <Typography variant="body2" color="text.secondary">
          This application’s own configuration. Values saved here are stored in the
          database and take priority over <code>.env</code>.
        </Typography>
      </Box>

      {error ? <Alert severity="error">{error}</Alert> : null}
      {saved ? (
        <Alert severity="success" onClose={() => setSaved(null)}>
          {saved}
        </Alert>
      ) : null}

      {/* ---- feed mode ---- */}
      <Card>
        <CardContent sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
              <Box>
                <Typography variant="h4">Feed mode</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  The synthetic feed generates prices locally with no upstream connection.
                  Useful without credentials and outside market hours. It is a mode of this
                  application, not a credential, which is why it lives here rather than on
                  a connection’s card.
                </Typography>
              </Box>
              <SourceChip source={settings?.syntheticFeedSource} />
            </Stack>

            <FormControlLabel
              control={
                <Switch
                  checked={syntheticFeed}
                  onChange={(event) => setSyntheticFeed(event.target.checked)}
                />
              }
              label={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2">
                    {syntheticFeed ? 'Synthetic feed (generated prices)' : 'Live Dhan market data'}
                  </Typography>
                  <Chip
                    size="small"
                    color={syntheticFeed ? 'warning' : 'success'}
                    variant="outlined"
                    label={syntheticFeed ? 'SYNTHETIC' : 'LIVE'}
                  />
                </Stack>
              }
            />

            {syntheticFeed ? (
              <Alert severity="info">
                Every price in the app will be locally generated and clearly marked, on
                every screen.
              </Alert>
            ) : wouldBeRefused ? (
              <Alert severity="error">
                <AlertTitle>Live mode needs Dhan credentials</AlertTitle>
                They live on{' '}
                <MuiLink component={RouterLink} to="/connections">
                  Connections
                </MuiLink>
                . Without them the feed reports an error rather than falling back to
                generated prices, so this save is refused.
              </Alert>
            ) : (
              <Alert severity="success">
                Live mode will use the Dhan connection’s credentials. Manage them on{' '}
                <MuiLink component={RouterLink} to="/connections">
                  Connections
                </MuiLink>
                .
              </Alert>
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* ---- where the credentials went ---- */}
      <Card>
        <CardContent sx={{ p: 3 }}>
          <Stack spacing={1.5}>
            <Typography variant="h4">Dhan credentials</Typography>
            <Typography variant="body2" color="text.secondary">
              These moved to Connections, which owns anything this application
              authenticates to and calls over the network — the client id, the access
              token, validation and the automatic renewal state.
            </Typography>
            <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
              <Chip
                size="small"
                variant="outlined"
                label={
                  credentialsPresent
                    ? `Configured · client ${settings.clientId}`
                    : 'Not configured'
                }
              />
              <Button
                size="small"
                variant="outlined"
                component={RouterLink}
                to="/connections"
              >
                Open Connections
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      <Stack direction="row" spacing={2} justifyContent="flex-end">
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={handleSave}
          disabled={saving || wouldBeRefused}
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </Stack>

      <Typography variant="caption" color="text.secondary">
        Saving restarts the market feed so changes take effect immediately. Connected
        browser tabs stay connected.
      </Typography>
    </Stack>
  );
}
