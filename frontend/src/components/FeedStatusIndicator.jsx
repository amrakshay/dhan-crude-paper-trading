import { Chip, Stack, Tooltip } from '@mui/material';
import CircleIcon from '@mui/icons-material/Circle';
import { useFeedHealth } from '../market/MarketFeedContext';
import { formatAge } from '../utils/format';

/**
 * Connection health and last-tick age.
 *
 * A stale book that looks live is the worst failure this tool can have, so the
 * age is always on screen next to the state rather than hidden in a tooltip.
 */
const STATE_STYLES = {
  CONNECTED: { color: 'success', label: 'Live' },
  CONNECTING: { color: 'warning', label: 'Connecting' },
  RECONNECTING: { color: 'warning', label: 'Reconnecting' },
  DISCONNECTED: { color: 'error', label: 'Disconnected' },
  ERROR: { color: 'error', label: 'Error' },
  SYNTHETIC: { color: 'warning', label: 'SYNTHETIC' },
  DISABLED: { color: 'default', label: 'Feed off' },
  // Running, with nothing to subscribe -- the ordinary overnight state. Grey,
  // not red: no strategy wants an instrument, so there is no socket and
  // nothing is wrong. Colouring it like a dropped connection would make this
  // indicator cry wolf every night.
  IDLE: { color: 'default', label: 'Idle' },
};

export default function FeedStatusIndicator() {
  const { state, lastTickAgeMs, stale, marketOpen, detail, subscribed, error } = useFeedHealth();
  const style = STATE_STYLES[state] ?? { color: 'default', label: state ?? 'Feed —' };

  const tooltip = [
    detail || error || `Feed ${style.label.toLowerCase()}`,
    subscribed !== null ? `${subscribed} instruments subscribed` : null,
    marketOpen === false ? 'Outside MCX hours (09:00–23:30 IST)' : null,
    stale ? 'No message from the server for over 5s' : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <Stack direction="row" spacing={1} alignItems="center">
      {marketOpen === false ? (
        <Tooltip title="MCX is closed. The book will not tick.">
          <Chip size="small" variant="outlined" label="Market closed" />
        </Tooltip>
      ) : null}
      <Tooltip title={tooltip}>
        <Chip
          size="small"
          color={stale && state !== 'SYNTHETIC' ? 'error' : style.color}
          variant={style.color === 'default' ? 'filled' : 'outlined'}
          icon={<CircleIcon sx={{ fontSize: 10 }} />}
          label={
            lastTickAgeMs !== null ? `${style.label} · ${formatAge(lastTickAgeMs)}` : style.label
          }
          className="numeric"
        />
      </Tooltip>
    </Stack>
  );
}
