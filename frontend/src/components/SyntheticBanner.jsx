import { Alert, AlertTitle } from '@mui/material';
import { useFeedHealth } from '../market/MarketFeedContext';

/**
 * Loud, permanent warning while prices are locally generated.
 *
 * A synthetic book that looks real is the fastest way to make this tool lie to
 * you, so this is deliberately not dismissible.
 */
export default function SyntheticBanner() {
  const { synthetic } = useFeedHealth();
  if (!synthetic) return null;

  return (
    <Alert severity="warning" variant="outlined" sx={{ mb: 3 }}>
      <AlertTitle sx={{ fontWeight: 600 }}>Synthetic data — not a real market</AlertTitle>
      Every price, size and greek on this screen is generated locally by a random walk. No
      upstream connection is open. Set <code>DHAN_SYNTHETIC_FEED=false</code> with valid{' '}
      <code>DHAN_CLIENT_ID</code> and <code>DHAN_ACCESS_TOKEN</code> in <code>.env</code> for
      live MCX data.
    </Alert>
  );
}
