import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  InputAdornment,
  LinearProgress,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import LockIcon from '@mui/icons-material/Lock';
import SaveIcon from '@mui/icons-material/Save';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import { useTheme } from '@mui/material/styles';
import { settingsApi } from '../api/settings';
import { formatCountdown } from '../utils/format';

/** Where a value came from, so the .env fallback is never a mystery. */
function SourceChip({ source }) {
  const label = { database: 'saved here', env: 'from .env', default: 'not set' }[source] ?? source;
  return <Chip size="small" variant="outlined" label={label} sx={{ height: 20 }} />;
}

/**
 * Live token countdown.
 *
 * Ticks locally off the absolute expiry returned by the API rather than
 * re-fetching, so the number keeps moving between saves.
 */
function TokenExpiry({ token }) {
  const theme = useTheme();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (!token?.present) return null;

  if (!token.isJwt || !token.expiresAt) {
    return (
      <Typography variant="body2" color="text.secondary">
        Stored, but its expiry could not be read — this token is not a JWT, so the time
        remaining is unknown.
      </Typography>
    );
  }

  const remaining = Math.floor((new Date(token.expiresAt).getTime() - now) / 1000);
  const expired = remaining <= 0;
  const critical = remaining > 0 && remaining < 2 * 3600;
  // Dhan tokens run ~24h; show the fraction left of that as a bar.
  const fraction = Math.max(0, Math.min(1, remaining / (24 * 3600)));
  const color = expired ? theme.market.down : critical ? theme.palette.warning.main : theme.market.up;

  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1.5} alignItems="baseline">
        <Typography variant="h4" className="numeric" sx={{ color }}>
          {expired ? 'Expired' : formatCountdown(remaining)}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {expired ? 'expired at' : 'expires at'}{' '}
          {new Date(token.expiresAt).toLocaleString('en-IN', { hour12: false })}
        </Typography>
      </Stack>
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
      {expired ? (
        <Alert severity="error" sx={{ mt: 1 }}>
          This token has expired. The live feed cannot connect until you paste a new one from
          the Dhan web console.
        </Alert>
      ) : critical ? (
        <Alert severity="warning" sx={{ mt: 1 }}>
          Less than two hours left. Dhan access tokens last about 24 hours — regenerate this
          one before your next session.
        </Alert>
      ) : null}
    </Stack>
  );
}

export default function SettingsPage() {
  const theme = useTheme();
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [syntheticFeed, setSyntheticFeed] = useState(true);
  const [clientId, setClientId] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [clearToken, setClearToken] = useState(false);

  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState(null);
  const [saved, setSaved] = useState(null);

  const load = useCallback(async () => {
    try {
      const result = await settingsApi.get();
      setSettings(result);
      setSyntheticFeed(result.syntheticFeed);
      setClientId(result.clientId ?? '');
      setAccessToken('');
      setClearToken(false);
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

  const tokenStored = Boolean(settings?.token?.present);
  // Credentials are only mandatory for the live feed.
  const credentialsRequired = !syntheticFeed;
  const missingClientId = credentialsRequired && !clientId.trim();
  const missingToken = credentialsRequired && !accessToken.trim() && (!tokenStored || clearToken);

  const canSave =
    !saving &&
    !missingClientId &&
    !missingToken &&
    (!accessToken.trim() || settings?.encryptionAvailable);

  const canValidate =
    !validating && Boolean(clientId.trim()) && (Boolean(accessToken.trim()) || tokenStored);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const result = await settingsApi.save({
        syntheticFeed,
        clientId: clientId.trim(),
        accessToken: accessToken.trim() || undefined,
        clearAccessToken: clearToken,
      });
      setSaved(result.message);
      setSettings(result.settings);
      setAccessToken('');
      setClearToken(false);
      setValidation(null);
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const handleValidate = async () => {
    setValidating(true);
    setValidation(null);
    try {
      setValidation(
        await settingsApi.validate({
          clientId: clientId.trim() || undefined,
          // Validate what is typed; fall back to the stored token.
          accessToken: accessToken.trim() || undefined,
        }),
      );
    } catch (validateError) {
      setValidation({ valid: false, message: validateError.message });
    } finally {
      setValidating(false);
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
          Dhan market-data credentials and feed mode. Values saved here are stored in the
          database and take priority over <code>.env</code>.
        </Typography>
      </Box>

      {error ? <Alert severity="error">{error}</Alert> : null}
      {saved ? (
        <Alert severity="success" onClose={() => setSaved(null)}>
          {saved}
        </Alert>
      ) : null}

      {settings && !settings.encryptionAvailable ? (
        <Alert severity="error" icon={<LockIcon />}>
          <AlertTitle>Cannot store an access token securely</AlertTitle>
          {settings.encryptionWarning}
        </Alert>
      ) : null}

      {settings?.token?.decryptFailed ? (
        <Alert severity="warning">
          A token is stored but could not be decrypted — the encryption secret has changed
          since it was saved. Paste the token again to replace it.
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
                  Useful without credentials and outside MCX hours (09:00–23:30 IST).
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
                Credentials below are optional while the synthetic feed is on. Every price in
                the app will be locally generated and clearly marked.
              </Alert>
            ) : (
              <Alert severity="warning">
                Live mode needs a valid client ID and access token. Dhan tokens expire about
                every 24 hours and must be regenerated in the Dhan web console.
              </Alert>
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* ---- credentials ---- */}
      <Card>
        <CardContent sx={{ p: 3 }}>
          <Stack spacing={3}>
            <Box>
              <Typography variant="h4">Dhan credentials</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                Market data only. These are used for the price feed, the option chain and the
                instrument master — never for trading.
              </Typography>
            </Box>

            <Stack spacing={1}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="subtitle2">Client ID</Typography>
                <SourceChip source={settings?.clientIdSource} />
              </Stack>
              <TextField
                size="small"
                fullWidth
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                placeholder="e.g. 1100123456"
                required={credentialsRequired}
                error={missingClientId}
                helperText={
                  missingClientId
                    ? 'Required when the synthetic feed is off'
                    : 'Stored as plain text.'
                }
              />
            </Stack>

            <Stack spacing={1}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="subtitle2">Access token</Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Tooltip title="Encrypted before it is written to the database, and never sent back to this page">
                    <Chip
                      size="small"
                      icon={<LockIcon sx={{ fontSize: 13 }} />}
                      label="encrypted at rest"
                      variant="outlined"
                      sx={{ height: 20 }}
                    />
                  </Tooltip>
                  <SourceChip source={settings?.accessTokenSource} />
                </Stack>
              </Stack>
              <TextField
                size="small"
                fullWidth
                type={showToken ? 'text' : 'password'}
                value={accessToken}
                onChange={(event) => {
                  setAccessToken(event.target.value);
                  if (event.target.value) setClearToken(false);
                }}
                placeholder={
                  tokenStored ? 'Leave blank to keep the stored token' : 'Paste the JWT from Dhan'
                }
                required={credentialsRequired && !tokenStored}
                error={missingToken}
                helperText={
                  missingToken
                    ? 'Required when the synthetic feed is off'
                    : tokenStored
                      ? `Stored: ${settings?.token?.masked}. Leave blank to keep it.`
                      : 'Regenerate this in the Dhan web console roughly every 24 hours.'
                }
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton size="small" onClick={() => setShowToken((value) => !value)}>
                        {showToken ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
              />
              {tokenStored && !accessToken ? (
                <FormControlLabel
                  control={
                    <Switch
                      size="small"
                      checked={clearToken}
                      onChange={(event) => setClearToken(event.target.checked)}
                    />
                  }
                  label={
                    <Typography variant="caption" color="text.secondary">
                      Remove the stored token
                    </Typography>
                  }
                />
              ) : null}
            </Stack>

            {tokenStored && !clearToken ? (
              <>
                <Divider />
                <Stack spacing={1}>
                  <Typography variant="subtitle2">Token validity</Typography>
                  <TokenExpiry token={settings.token} />
                  {settings.token?.dhanClientId ? (
                    <Typography variant="caption" color="text.secondary">
                      Issued for client ID{' '}
                      <strong className="numeric">{settings.token.dhanClientId}</strong>
                    </Typography>
                  ) : null}
                </Stack>
              </>
            ) : null}

            {validation ? (
              <Alert
                severity={validation.valid ? 'success' : 'error'}
                icon={validation.valid ? <CheckCircleIcon /> : <ErrorIcon />}
                onClose={() => setValidation(null)}
              >
                {validation.message}
                {validation.detail && !validation.valid ? (
                  <Typography variant="caption" component="div" sx={{ mt: 0.5, opacity: 0.8 }}>
                    {validation.detail}
                  </Typography>
                ) : null}
              </Alert>
            ) : null}

            <Divider />

            <Stack direction="row" spacing={2} justifyContent="flex-end" flexWrap="wrap">
              <Button
                variant="outlined"
                startIcon={validating ? <CircularProgress size={16} /> : <VerifiedUserIcon />}
                onClick={handleValidate}
                disabled={!canValidate}
              >
                {validating ? 'Checking with Dhan…' : 'Validate token'}
              </Button>
              <Button
                variant="contained"
                startIcon={<SaveIcon />}
                onClick={handleSave}
                disabled={!canSave}
              >
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      <Typography variant="caption" color="text.secondary">
        Saving restarts the market feed so changes take effect immediately. Connected browser
        tabs stay connected.
      </Typography>
    </Stack>
  );
}
