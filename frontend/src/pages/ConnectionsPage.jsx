import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Grid,
  IconButton,
  InputAdornment,
  LinearProgress,
  Link as MuiLink,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import CloseIcon from '@mui/icons-material/Close';
import HearingIcon from '@mui/icons-material/Hearing';
import LockIcon from '@mui/icons-material/Lock';
import SaveIcon from '@mui/icons-material/Save';
import SendIcon from '@mui/icons-material/Send';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import TelegramIcon from '@mui/icons-material/Telegram';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { Link as RouterLink } from 'react-router-dom';
import { connectionsApi } from '../api/connections';
import { usersApi } from '../api/users';
import { formatCountdown } from '../utils/format';

/**
 * `/connections` -- one page that owns anything this application authenticates
 * to and calls over the network.
 *
 * Two things talked to a third party before this existed and neither lived
 * anywhere called a connection: the Dhan credentials sat on Settings beside a
 * switch that has nothing to do with credentials, and there was nowhere at all
 * to put a second provider.
 *
 * Three rules it is written to, beyond the usual ones:
 *
 * - **The status pill is LAST KNOWN, with its age.** Opening this page costs no
 *   network call. Every card shows the most recent check AND how long ago it
 *   was; a check that has never run says so rather than showing a green pill,
 *   and a connection that has gone quiet shows its last result with its age.
 *   Same rule `FeedStatusIndicator` already follows (frontend/CLAUDE.md §3).
 * - **The pill has more than two states.** Two would force "not set up yet" and
 *   "set up and broken" into the same red, and those are the two an operator
 *   most needs to tell apart.
 * - **Discovering an id grants it nothing.** The listen test finds a Telegram
 *   user id; mapping it to an application user stays an explicit action.
 *
 * Admin-only: `/connections` is in `conf/role-pages.json` for
 * ROLE_ACCOUNT_ADMIN alone, and `require_admin` on every route is what actually
 * refuses. It is deliberately NOT strategy-gated -- it is how you fix the
 * credentials the strategies run on, so a toggle must never hide it.
 *
 * There is no search box. The reference design has one because it has dozens
 * of cards; with two it would be furniture. Add it when it earns its place.
 */

/** How long the listening window stays open. Long enough to unlock a phone. */
const LISTEN_SECONDS = 60;

const PROVIDER_ICONS = {
  dhan: ShowChartIcon,
  telegram: TelegramIcon,
};

/** Every pill state, with the tone it is drawn in. */
const STATUS_TONES = {
  CONNECTED: 'up',
  NOT_CONFIGURED: 'neutral',
  NEVER_CHECKED: 'neutral',
  EXPIRING_SOON: 'warning',
  ERROR: 'down',
  DISABLED: 'neutral',
};

const STATUS_LABELS = {
  CONNECTED: 'Connected',
  NOT_CONFIGURED: 'Not configured',
  NEVER_CHECKED: 'Never checked',
  EXPIRING_SOON: 'Expiring soon',
  ERROR: 'Error',
  DISABLED: 'Switched off',
};

/**
 * "2 minutes ago", or the honest absence.
 *
 * Null is NOT "just now": a connection nobody has ever checked has no age, and
 * rendering that as a fresh timestamp is exactly the lie decision 3 is about.
 */
function ageLabel(iso, now) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function StatusPill({ status }) {
  const theme = useTheme();
  const tone = STATUS_TONES[status] ?? 'neutral';
  const colours = {
    up: { bg: theme.market.upSoft, fg: theme.market.up },
    down: { bg: theme.market.downSoft, fg: theme.market.down },
    warning: { bg: theme.palette.warning.light, fg: theme.palette.warning.dark },
    neutral: { bg: theme.palette.action.hover, fg: theme.palette.text.secondary },
  }[tone];

  return (
    <Chip
      size="small"
      label={STATUS_LABELS[status] ?? status}
      // MuiChip sets a background on every chip in this theme, which defeats
      // `color="primary"` -- so bgcolor and color are set explicitly
      // (frontend/CLAUDE.md §1).
      sx={{ bgcolor: colours.bg, color: colours.fg, fontWeight: 600, height: 22 }}
    />
  );
}

function ConnectionCardTile({ card, now, onOpen }) {
  const theme = useTheme();
  const Icon = PROVIDER_ICONS[card.provider] ?? ShowChartIcon;
  const age = ageLabel(card.lastCheckedAt, now);

  return (
    <Card sx={{ height: '100%' }}>
      <CardActionArea onClick={() => onOpen(card.provider)} sx={{ height: '100%' }}>
        <CardContent>
          <Stack direction="row" spacing={2} alignItems="flex-start">
            <Box
              sx={{
                width: 40,
                height: 40,
                borderRadius: 1,
                display: 'grid',
                placeItems: 'center',
                bgcolor: theme.palette.action.hover,
                flexShrink: 0,
              }}
            >
              <Icon fontSize="small" sx={{ color: theme.palette.text.secondary }} />
            </Box>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="flex-start"
                spacing={1}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 600 }} noWrap>
                    {card.label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap display="block">
                    {card.subtitle}
                  </Typography>
                </Box>
                <StatusPill status={card.status} />
              </Stack>
            </Box>
          </Stack>

          <Divider sx={{ my: 1.5 }} />

          <Stack direction="row" justifyContent="space-between" alignItems="flex-end">
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" display="block">
                {card.metricLabel}
              </Typography>
              <Typography variant="body2" className="numeric" noWrap>
                {card.metricValue ?? (
                  <Typography component="span" variant="body2" color="text.disabled">
                    not set
                  </Typography>
                )}
              </Typography>
            </Box>
            <Box sx={{ textAlign: 'right' }}>
              <Typography variant="caption" color="text.secondary" display="block">
                Last checked
              </Typography>
              {/* Decision 3: the AGE, not a green dot. A card with no check
                  says so rather than looking live. */}
              <Typography variant="body2">
                {age ?? (
                  <Typography component="span" variant="body2" color="text.disabled">
                    never
                  </Typography>
                )}
              </Typography>
            </Box>
          </Stack>

          <Stack direction="row" spacing={0.5} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
            {card.capabilities.map((capability) => (
              <Chip
                key={capability}
                size="small"
                variant="outlined"
                label={capability}
                sx={{ height: 20, fontSize: 11, letterSpacing: 0.4 }}
              />
            ))}
          </Stack>

          <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
            {card.statusDetail}
          </Typography>
        </CardContent>
      </CardActionArea>
    </Card>
  );
}

/** Live countdown off the absolute expiry, the Settings-page trick. */
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
        remaining is unknown, and automatic renewal cannot be timed.
      </Typography>
    );
  }

  const remaining = Math.floor((new Date(token.expiresAt).getTime() - now) / 1000);
  const expired = remaining <= 0;
  const critical = remaining > 0 && remaining < 6 * 3600;
  const fraction = Math.max(0, Math.min(1, remaining / (24 * 3600)));
  const colour = expired
    ? theme.market.down
    : critical
      ? theme.palette.warning.main
      : theme.market.up;

  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1.5} alignItems="baseline">
        <Typography variant="h4" className="numeric" sx={{ color: colour }}>
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
          '& .MuiLinearProgress-bar': { bgcolor: colour },
        }}
      />
    </Stack>
  );
}

function SecretField({ label, value, stored, onChange, helper, placeholder }) {
  const [visible, setVisible] = useState(false);
  return (
    <Stack spacing={1}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="subtitle2">{label}</Typography>
        <Tooltip title="Encrypted before it is written to the database, and never sent back to this page">
          <Chip
            size="small"
            variant="outlined"
            icon={<LockIcon sx={{ fontSize: 13 }} />}
            label="encrypted at rest"
            sx={{ height: 20 }}
          />
        </Tooltip>
      </Stack>
      <TextField
        size="small"
        fullWidth
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={stored?.present ? 'Leave blank to keep the stored one' : placeholder}
        helperText={
          stored?.present ? `Stored: ${stored.masked}. Leave blank to keep it.` : helper
        }
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton size="small" onClick={() => setVisible((shown) => !shown)}>
                {visible ? (
                  <VisibilityOffIcon fontSize="small" />
                ) : (
                  <VisibilityIcon fontSize="small" />
                )}
              </IconButton>
            </InputAdornment>
          ),
        }}
      />
    </Stack>
  );
}

// --- Dhan -------------------------------------------------------------------
function DhanDetail({ card, onSaved }) {
  const [clientId, setClientId] = useState(card.detail?.clientId ?? '');
  const [accessToken, setAccessToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const renewal = card.detail?.autoRenew ?? {};

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await connectionsApi.save('dhan', {
        settings: {
          client_id: clientId.trim(),
          // Omitted means "keep the stored token", never "clear it".
          ...(accessToken.trim() ? { access_token: accessToken.trim() } : {}),
        },
      });
      setAccessToken('');
      setResult({ ok: true, message: 'Saved. The market feed was restarted.' });
      onSaved(saved);
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setBusy(false);
    }
  };

  const validate = async () => {
    setBusy(true);
    setResult(null);
    try {
      const outcome = await connectionsApi.validate('dhan', {
        ...(clientId.trim() ? { client_id: clientId.trim() } : {}),
        ...(accessToken.trim() ? { access_token: accessToken.trim() } : {}),
      });
      setResult({ ok: outcome.valid, message: outcome.message });
    } catch (validateError) {
      setResult({ ok: false, message: validateError.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack spacing={3}>
      <Alert severity="info">
        Market data only. These are used for the price feed, the option chain, the chart
        and the overnight bar refresh — never for trading. The synthetic-feed switch
        stays on{' '}
        <MuiLink component={RouterLink} to="/settings">
          Settings
        </MuiLink>
        : it is a mode of this application, not a credential.
      </Alert>

      {error ? <Alert severity="error">{error}</Alert> : null}
      {result ? (
        <Alert severity={result.ok ? 'success' : 'error'} onClose={() => setResult(null)}>
          {result.message}
        </Alert>
      ) : null}

      <Stack spacing={1}>
        <Typography variant="subtitle2">Client ID</Typography>
        <TextField
          size="small"
          fullWidth
          value={clientId}
          onChange={(event) => setClientId(event.target.value)}
          placeholder="e.g. 1100123456"
          helperText={`Stored as plain text. Currently from: ${card.detail?.clientIdSource ?? 'default'}.`}
        />
      </Stack>

      <SecretField
        label="Access token"
        value={accessToken}
        stored={card.detail?.token}
        onChange={setAccessToken}
        placeholder="Paste the JWT from Dhan"
        helper="Generate this on Dhan Web. It lasts about 24 hours and is renewed automatically."
      />

      {card.detail?.token?.present ? (
        <>
          <Divider />
          <Stack spacing={1}>
            <Typography variant="subtitle2">Token validity</Typography>
            <TokenExpiry token={card.detail.token} />
            {/* Three outcomes, kept apart: renewed, will retry, and needs a
                human. Only the third is a problem somebody has to act on. */}
            <Typography variant="caption" color="text.secondary">
              Automatic renewal is {renewal.enabled ? 'on' : 'off'}
              {renewal.enabled
                ? `, taking over under ${renewal.renewBeforeHours}h. ${renewal.renewals ?? 0} renewal(s) so far.`
                : '.'}
            </Typography>
            {renewal.lastOutcome ? (
              <Typography variant="caption" color="text.secondary">
                Last check: {renewal.lastOutcome}
              </Typography>
            ) : null}
            {renewal.lastError ? (
              <Alert severity="warning">Renewal reported: {renewal.lastError}</Alert>
            ) : null}
            <Typography variant="caption" color="text.disabled">
              This application can renew a token it already holds but cannot mint one — a
              token allowed to lapse entirely has to be pasted in by hand. That was an
              explicit decision: the alternative meant storing your Dhan PIN.
            </Typography>
          </Stack>
        </>
      ) : null}

      <Stack direction="row" spacing={2} justifyContent="flex-end">
        <Button
          variant="outlined"
          startIcon={busy ? <CircularProgress size={16} /> : <VerifiedUserIcon />}
          onClick={validate}
          disabled={busy}
        >
          Validate
        </Button>
        <Button variant="contained" startIcon={<SaveIcon />} onClick={save} disabled={busy}>
          Save
        </Button>
      </Stack>
    </Stack>
  );
}

// --- Telegram ---------------------------------------------------------------
function ListenResult({ result, onUseChat, onMapUser, applying }) {
  if (!result) return null;

  if (!result.received) {
    // Nothing arriving is a RESULT, not an error. The server sends the reasons
    // in the order they are likely; the page does not compose its own.
    return (
      <Alert severity="info">
        <AlertTitle>Nothing arrived</AlertTitle>
        {result.message}
      </Alert>
    );
  }

  const update = result.update ?? {};
  return (
    <Alert severity="success">
      <AlertTitle>Received</AlertTitle>
      <Stack spacing={0.5} sx={{ mb: 1 }}>
        <Typography variant="body2">
          Sender user id:{' '}
          <strong className="numeric">{update.senderId ?? 'none — this was a channel post'}</strong>
        </Typography>
        <Typography variant="body2">
          Sender: {update.senderName ?? '—'}
          {update.senderUsername ? ` (@${update.senderUsername})` : ''}
        </Typography>
        <Typography variant="body2">
          Chat: <span className="numeric">{update.chatId}</span> ({update.chatType})
          {update.chatTitle ? ` · ${update.chatTitle}` : ''}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Message: {update.text ?? '—'}
        </Typography>
      </Stack>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        {result.message}
      </Typography>
      {/* One-click SUGGESTIONS. Applying stays an explicit action: discovering
          an id must never be what grants it anything. */}
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {update.chatId ? (
          <Button size="small" variant="outlined" onClick={() => onUseChat(update.chatId, update.chatTitle)}>
            Use this chat as the alert channel
          </Button>
        ) : null}
        {update.senderId ? (
          <Button
            size="small"
            variant="outlined"
            onClick={() => onMapUser(update.senderId)}
            disabled={applying}
          >
            Map this Telegram user to an account…
          </Button>
        ) : null}
      </Stack>
    </Alert>
  );
}

function TelegramDetail({ card, onSaved }) {
  const detail = card.detail ?? {};
  const [botToken, setBotToken] = useState('');
  const [chatId, setChatId] = useState(detail.chatId ?? '');
  const [chatTitle, setChatTitle] = useState(detail.chatTitle ?? '');
  const [commandsEnabled, setCommandsEnabled] = useState(Boolean(detail.commandsEnabled));
  const [controlEnabled, setControlEnabled] = useState(Boolean(detail.controlCommandsEnabled));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const [listening, setListening] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [listenResult, setListenResult] = useState(null);

  const [users, setUsers] = useState([]);
  const [mappingFor, setMappingFor] = useState(null);

  useEffect(() => {
    usersApi
      .list()
      .then((response) => setUsers(response.users ?? []))
      .catch(() => setUsers([]));
  }, []);

  // The countdown ticks locally, so a window that is running looks like it is.
  useEffect(() => {
    if (!listening) return undefined;
    const timer = window.setInterval(() => setRemaining((left) => Math.max(0, left - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [listening]);

  const save = async (overrides = {}) => {
    setBusy(true);
    setError(null);
    try {
      const saved = await connectionsApi.save('telegram', {
        settings: {
          chat_id: chatId.trim(),
          chat_title: chatTitle.trim(),
          commands_enabled: commandsEnabled ? 'true' : 'false',
          control_commands_enabled: controlEnabled ? 'true' : 'false',
          ...(botToken.trim() ? { bot_token: botToken.trim() } : {}),
          ...overrides,
        },
      });
      setBotToken('');
      setResult({ severity: 'success', message: 'Saved.' });
      onSaved(saved);
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setBusy(false);
    }
  };

  const validate = async () => {
    setBusy(true);
    setResult(null);
    try {
      const outcome = await connectionsApi.validate('telegram', {
        ...(botToken.trim() ? { bot_token: botToken.trim() } : {}),
        ...(chatId.trim() ? { chat_id: chatId.trim() } : {}),
      });
      setResult({
        severity: outcome.valid ? 'success' : 'error',
        message: outcome.message,
        detail: outcome.detail,
      });
    } catch (validateError) {
      setResult({ severity: 'error', message: validateError.message });
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    setBusy(true);
    setResult(null);
    try {
      const outcome = await connectionsApi.sendTestMessage();
      setResult({ severity: outcome.sent ? 'success' : 'error', message: outcome.message });
    } catch (sendError) {
      setResult({ severity: 'error', message: sendError.message });
    } finally {
      setBusy(false);
    }
  };

  const listen = async () => {
    setListening(true);
    setListenResult(null);
    setRemaining(LISTEN_SECONDS);
    try {
      setListenResult(await connectionsApi.listen(LISTEN_SECONDS));
    } catch (listenError) {
      setListenResult({ received: false, message: listenError.message });
    } finally {
      setListening(false);
      setRemaining(0);
    }
  };

  const mapUser = async (userId, telegramUserId) => {
    setBusy(true);
    try {
      await usersApi.update(userId, { telegramUserId });
      const refreshed = await connectionsApi.get('telegram');
      onSaved(refreshed);
      setMappingFor(null);
      setResult({
        severity: 'success',
        message:
          'Mapped. That account’s existing role now applies to commands it sends — ' +
          'read-only for a user, control for an administrator.',
      });
    } catch (mapError) {
      setResult({ severity: 'error', message: mapError.message });
    } finally {
      setBusy(false);
    }
  };

  const bot = result?.detail?.bot;

  return (
    <Stack spacing={3}>
      {error ? <Alert severity="error">{error}</Alert> : null}
      {result ? (
        <Alert severity={result.severity} onClose={() => setResult(null)}>
          {result.message}
        </Alert>
      ) : null}

      <SecretField
        label="Bot token"
        value={botToken}
        stored={detail.botTokenStored ?? card.settings?.bot_token}
        onChange={setBotToken}
        placeholder="123456789:AA…"
        helper="Message @BotFather on Telegram, send /newbot, and paste the token it gives you."
      />

      <Stack spacing={1}>
        <Typography variant="subtitle2">Channel or chat id</Typography>
        <TextField
          size="small"
          fullWidth
          value={chatId}
          onChange={(event) => setChatId(event.target.value)}
          placeholder="-1001234567890"
          helperText="A channel id is a negative number beginning -100. Use “Listen for a test message” to discover one."
        />
        <TextField
          size="small"
          fullWidth
          value={chatTitle}
          onChange={(event) => setChatTitle(event.target.value)}
          placeholder="NSE Swing Alerts"
          label="Channel name (shown on the card and in test results)"
        />
      </Stack>

      {bot ? (
        <Alert severity="info">
          Bot: <strong>@{bot.username}</strong> (id {bot.id}).{' '}
          {/* Reported as a FACT rather than left for the operator to guess at:
              getMe returns can_read_all_group_messages. */}
          {bot.canReadAllGroupMessages
            ? 'Privacy mode is OFF, so it can see ordinary group messages.'
            : 'Privacy mode is ON, so in a GROUP it sees only commands and replies. Direct messages are unaffected.'}
        </Alert>
      ) : null}

      <Divider />

      {/* --- the two test buttons ---------------------------------------- */}
      <Stack spacing={2}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          Prove the two halves separately
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Sending and receiving fail for completely different reasons, so they are
          checked separately. Neither runs on its own — both are explicit presses.
        </Typography>

        <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
          <Button
            variant="outlined"
            startIcon={busy ? <CircularProgress size={16} /> : <SendIcon />}
            onClick={sendTest}
            disabled={busy || listening}
          >
            Send test message
          </Button>
          <Button
            variant="outlined"
            startIcon={listening ? <CircularProgress size={16} /> : <HearingIcon />}
            onClick={listen}
            disabled={busy || listening}
          >
            {listening ? `Listening… ${remaining}s` : 'Listen for a test message'}
          </Button>
        </Stack>

        {listening ? (
          <Alert severity="info" icon={<HearingIcon />}>
            <AlertTitle>Listening for {remaining}s</AlertTitle>
            {/* The instruction that saves an hour. A channel post carries NO
                user, so posting in the channel teaches you nothing about your
                own user id -- which is the thing you came for. */}
            <strong>Send a direct message to your bot now</strong> — not a post in the
            channel. A channel post carries no sender, so it would give you the chat id
            and nothing about your own user id.
            <Typography variant="caption" display="block" sx={{ mt: 1 }}>
              A bot cannot message a person first, so you must have pressed Start in its
              chat at least once.
            </Typography>
            <LinearProgress
              variant="determinate"
              value={((LISTEN_SECONDS - remaining) / LISTEN_SECONDS) * 100}
              sx={{ mt: 1.5, height: 6, borderRadius: 3 }}
            />
          </Alert>
        ) : null}

        <ListenResult
          result={listenResult}
          applying={busy}
          onUseChat={(id, title) => {
            setChatId(String(id));
            if (title) setChatTitle(title);
          }}
          onMapUser={(telegramUserId) => setMappingFor(telegramUserId)}
        />
      </Stack>

      <Divider />

      {/* --- commands ----------------------------------------------------- */}
      <Stack spacing={1.5}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          Commands in
        </Typography>
        <Typography variant="caption" color="text.secondary">
          A channel id identifies a destination, not a person. A command is authorised by
          resolving its Telegram sender to an account here and applying that account’s
          existing role — so deactivating somebody stops their commands too. Nobody is
          mapped by default, and a connection with alerts working and no command users is
          a complete, normal state.
        </Typography>

        <FormControlLabel
          control={
            <Switch
              checked={commandsEnabled}
              onChange={(event) => setCommandsEnabled(event.target.checked)}
            />
          }
          label={
            <Typography variant="body2">
              Read commands (<code>/status</code>, <code>/book</code>, <code>/pnl</code>,{' '}
              <code>/health</code>)
            </Typography>
          }
        />
        <FormControlLabel
          control={
            <Switch
              checked={controlEnabled}
              onChange={(event) => setControlEnabled(event.target.checked)}
              disabled={!commandsEnabled}
            />
          }
          label={
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2">
                Control commands (<code>/arm</code>, <code>/disarm</code>, <code>/run</code>)
              </Typography>
              <Chip size="small" variant="outlined" color="warning" label="changes what it does with money" sx={{ height: 20 }} />
            </Stack>
          }
        />
        {controlEnabled ? (
          <Alert severity="warning">
            Control commands need an account administrator — the same gate the arming
            button uses. A Telegram message that arms a strategy is not a thinner path
            than the button, and every one is journalled with its sender.
          </Alert>
        ) : null}

        <Typography variant="subtitle2" sx={{ mt: 1 }}>
          Who may issue a command
        </Typography>
        {(detail.commandUsers ?? []).length === 0 ? (
          <Typography variant="body2" color="text.disabled">
            Nobody. Use “Listen for a test message” to discover a Telegram user id, then
            map it to an account — mapping is an explicit action on the Users page or
            from the result above.
          </Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Account</TableCell>
                  <TableCell>Role</TableCell>
                  <TableCell align="right">Telegram user id</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {detail.commandUsers.map((user) => (
                  <TableRow key={user.userId}>
                    <TableCell>
                      {user.name}
                      <Typography variant="caption" color="text.secondary" display="block">
                        {user.email} · {user.status}
                      </Typography>
                    </TableCell>
                    <TableCell>{user.role}</TableCell>
                    <TableCell align="right" className="numeric">
                      {user.telegramUserId}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Stack>

      <Stack direction="row" spacing={2} justifyContent="flex-end">
        <Button
          variant="outlined"
          startIcon={busy ? <CircularProgress size={16} /> : <VerifiedUserIcon />}
          onClick={validate}
          disabled={busy || listening}
        >
          Validate
        </Button>
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={() => save()}
          disabled={busy || listening}
        >
          Save
        </Button>
      </Stack>

      {/* Mapping a discovered id: still an explicit choice of WHICH account. */}
      <Dialog open={mappingFor !== null} onClose={() => setMappingFor(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Map Telegram user {mappingFor}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Their commands will run with the account’s existing role. Discovering an id
            grants nothing until you choose here.
          </Typography>
          <Stack spacing={1}>
            {users.map((user) => (
              <Button
                key={user.id}
                variant="outlined"
                onClick={() => mapUser(user.id, mappingFor)}
                disabled={busy}
              >
                {user.fullName} — {user.role}
              </Button>
            ))}
          </Stack>
        </DialogContent>
      </Dialog>
    </Stack>
  );
}

// --- the page ---------------------------------------------------------------
export default function ConnectionsPage() {
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [detail, setDetail] = useState(null);
  const [now, setNow] = useState(() => Date.now());

  // The age ticks locally so a card that has not been re-checked visibly ages,
  // rather than reading "0s ago" until the next poll.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const load = useCallback(async () => {
    try {
      const response = await connectionsApi.list();
      setCards(response.connections ?? []);
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

  const openDetail = async (provider) => {
    setOpen(provider);
    setDetail(null);
    try {
      setDetail(await connectionsApi.get(provider));
    } catch (detailError) {
      setError(detailError.message);
      setOpen(null);
    }
  };

  const onSaved = (saved) => {
    setDetail(saved);
    load();
  };

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h2">Connections</Typography>
        <Typography variant="body2" color="text.secondary">
          Everything this application authenticates to and calls over the network. Status
          is the <strong>last known</strong> result with its age — opening this page costs
          no network call, and a card that has never been checked says so rather than
          showing green.
        </Typography>
      </Box>

      {error ? <Alert severity="error">{error}</Alert> : null}

      <Grid container spacing={2}>
        {cards.map((card) => (
          <Grid item xs={12} sm={6} lg={4} key={card.provider}>
            <ConnectionCardTile card={card} now={now} onOpen={openDetail} />
          </Grid>
        ))}
      </Grid>

      <Dialog
        open={open !== null}
        onClose={() => setOpen(null)}
        maxWidth="md"
        fullWidth
        scroll="paper"
      >
        <DialogTitle>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Stack direction="row" spacing={1.5} alignItems="center">
              <Typography variant="h4">{detail?.label ?? open}</Typography>
              {detail ? <StatusPill status={detail.status} /> : null}
            </Stack>
            <IconButton size="small" onClick={() => setOpen(null)}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Stack>
          {detail ? (
            <Typography variant="caption" color="text.secondary">
              {detail.statusDetail}
            </Typography>
          ) : null}
        </DialogTitle>
        <DialogContent dividers>
          {!detail ? (
            <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 200 }}>
              <CircularProgress />
            </Box>
          ) : detail.provider === 'dhan' ? (
            <DhanDetail card={detail} onSaved={onSaved} />
          ) : (
            <TelegramDetail card={detail} onSaved={onSaved} />
          )}
        </DialogContent>
      </Dialog>
    </Stack>
  );
}
