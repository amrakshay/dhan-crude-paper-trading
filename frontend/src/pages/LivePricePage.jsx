import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  Stack,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import ArrowDropUpIcon from '@mui/icons-material/ArrowDropUp';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { useTheme } from '@mui/material/styles';
import { instrumentsApi } from '../api/instruments';
import { useFeedHealth, useMarketRow } from '../market/MarketFeedContext';
import SyntheticBanner from '../components/SyntheticBanner';
import DepthLadder from '../components/DepthLadder';
import PriceChart from '../components/PriceChart';
import {
  computeChange,
  formatAge,
  formatCompact,
  formatPercent,
  formatPrice,
  formatQty,
  formatSignedPrice,
  formatTime,
} from '../utils/format';

function StatTile({ label, value, sub, color }) {
  return (
    <Stack spacing={0.25}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h5" className="numeric" sx={{ color }}>
        {value}
      </Typography>
      {sub ? (
        <Typography variant="caption" color="text.secondary" className="numeric">
          {sub}
        </Typography>
      ) : null}
    </Stack>
  );
}

export default function LivePricePage() {
  const theme = useTheme();
  const [future, setFuture] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const row = useMarketRow(future?.securityId);
  const { lastTickAgeMs, stale, marketOpen, state } = useFeedHealth();

  const loadFuture = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setFuture(await instrumentsApi.nearFuture());
    } catch (error) {
      setFuture(null);
      setLoadError(error.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFuture();
  }, []);

  const handleRefreshMaster = async () => {
    setRefreshing(true);
    try {
      await instrumentsApi.refresh(false);
      await loadFuture();
    } catch (error) {
      setLoadError(error.message);
    } finally {
      setRefreshing(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
        <CircularProgress />
      </Box>
    );
  }

  const change = computeChange(row);
  const isUp = (change.absolute ?? 0) >= 0;
  const changeColor = change.absolute === null ? 'text.primary' : isUp ? theme.market.up : theme.market.down;

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
        <Box>
          {/* Named for what it shows, matching the sidebar. The route and
              the component keep their old names: renaming a path breaks
              every saved link and every role-pages entry pointing at it. */}
          <Typography variant="h2">Crude Oil</Typography>
          <Typography variant="body2" color="text.secondary">
            {future
              ? `${future.displayName ?? future.tradingSymbol} · expires ${future.expiryDate}`
              : 'The near-month future'}
          </Typography>
        </Box>
        <Button
          startIcon={<RefreshIcon />}
          onClick={handleRefreshMaster}
          disabled={refreshing}
          variant="outlined"
        >
          {refreshing ? 'Refreshing…' : 'Refresh instruments'}
        </Button>
      </Stack>

      <SyntheticBanner />

      {loadError ? (
        <Alert severity="error" action={<Button onClick={handleRefreshMaster}>Load master</Button>}>
          {loadError}
        </Alert>
      ) : null}

      {marketOpen === false ? (
        <Alert severity="info">
          MCX is outside its trading window (09:00–23:30 IST, Mon–Fri). Prices below are the
          last values received, not current quotes.
        </Alert>
      ) : null}

      {stale && state !== 'SYNTHETIC' ? (
        <Alert severity="warning">
          No update from the server for over 5 seconds. The numbers below may be stale.
        </Alert>
      ) : null}

      {!row ? (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary">
              Waiting for the first tick on {future?.securityId ?? 'the near future'}…
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Grid container spacing={3}>
          <Grid item xs={12} lg={7}>
            <Card sx={{ height: '100%' }}>
              <CardContent sx={{ p: 3 }}>
                <Stack spacing={3}>
                  <Stack direction="row" alignItems="flex-end" spacing={2} flexWrap="wrap">
                    <Typography variant="h1" className="numeric" sx={{ fontSize: '2.75rem', lineHeight: 1 }}>
                      {formatPrice(row.ltp)}
                    </Typography>
                    <Stack direction="row" alignItems="center" sx={{ color: changeColor, mb: 0.5 }}>
                      {change.absolute !== null ? (isUp ? <ArrowDropUpIcon /> : <ArrowDropDownIcon />) : null}
                      <Typography variant="h5" className="numeric" sx={{ color: changeColor }}>
                        {formatSignedPrice(change.absolute)} ({formatPercent(change.percent)})
                      </Typography>
                    </Stack>
                    <Box sx={{ flexGrow: 1 }} />
                    <Chip
                      size="small"
                      variant="outlined"
                      className="numeric"
                      label={`tick age ${formatAge(lastTickAgeMs)}`}
                      color={stale ? 'error' : 'default'}
                    />
                  </Stack>

                  <Divider />

                  <Grid container spacing={3}>
                    <Grid item xs={6} sm={3}>
                      <StatTile label="Open" value={formatPrice(row.open)} />
                    </Grid>
                    <Grid item xs={6} sm={3}>
                      <StatTile label="High" value={formatPrice(row.high)} color={theme.market.up} />
                    </Grid>
                    <Grid item xs={6} sm={3}>
                      <StatTile label="Low" value={formatPrice(row.low)} color={theme.market.down} />
                    </Grid>
                    <Grid item xs={6} sm={3}>
                      <StatTile label="Prev Close" value={formatPrice(row.close)} />
                    </Grid>
                    <Grid item xs={6} sm={3}>
                      <StatTile label="Volume" value={formatCompact(row.volume)} sub="barrels" />
                    </Grid>
                    <Grid item xs={6} sm={3}>
                      <StatTile label="Open Interest" value={formatCompact(row.oi)} sub="lots" />
                    </Grid>
                    <Grid item xs={6} sm={3}>
                      <StatTile
                        label="Best Bid"
                        value={formatPrice(row.bid)}
                        sub={`${formatQty(row.bidQty)} qty`}
                        color={theme.market.up}
                      />
                    </Grid>
                    <Grid item xs={6} sm={3}>
                      <StatTile
                        label="Best Ask"
                        value={formatPrice(row.ask)}
                        sub={`${formatQty(row.askQty)} qty`}
                        color={theme.market.down}
                      />
                    </Grid>
                  </Grid>

                  <Divider />

                  <Stack direction="row" spacing={3} flexWrap="wrap">
                    <Typography variant="caption" color="text.secondary">
                      Security ID <strong className="numeric">{row.securityId}</strong>
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Lot size <strong className="numeric">{formatQty(row.lotSize)}</strong> barrels
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Tick <strong className="numeric">{formatPrice(row.tickSize)}</strong>
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Last update <strong className="numeric">{formatTime(row.ts)}</strong>
                    </Typography>
                  </Stack>
                </Stack>
              </CardContent>
            </Card>
          </Grid>

          <Grid item xs={12} lg={5}>
            <Card sx={{ height: '100%' }}>
              <CardContent sx={{ p: 3 }}>
                <Typography variant="h4" sx={{ mb: 2 }}>
                  Market Depth
                </Typography>
                <DepthLadder depth={row.depth} />
                <Stack direction="row" justifyContent="space-between" sx={{ mt: 2 }}>
                  <Typography variant="caption" color="text.secondary" className="numeric">
                    Total buy {formatCompact(row.totalBuyQty)}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" className="numeric">
                    Total sell {formatCompact(row.totalSellQty)}
                  </Typography>
                </Stack>
              </CardContent>
            </Card>
          </Grid>
        </Grid>
      )}

      <PriceChart
        securityId={future?.securityId}
        title="Price chart"
        subtitle={future?.displayName ?? future?.tradingSymbol ?? undefined}
      />
    </Stack>
  );
}
