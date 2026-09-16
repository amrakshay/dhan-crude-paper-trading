/**
 * Candlestick chart for one instrument.
 *
 * History comes from the backend (`/api/market/candles`, which reads Dhan's
 * read-only chart endpoints or generates bars when the feed is synthetic). The
 * newest bar is then updated from the SHARED WebSocket -- this component opens
 * no socket of its own and polls nothing.
 *
 * The re-render contract (frontend/CLAUDE.md section 2) is why this file is
 * shaped the way it is:
 *
 *   * Candles never enter React state. They go straight into the series via
 *     `setData` once, and after that only `series.update()` is called, which is
 *     a cheap imperative call rather than a render.
 *   * The component that subscribes to the feed is `<LiveCandle>`, which
 *     renders `null`. It re-renders at the feed's ~10/sec, the chart chrome
 *     does not.
 *
 * Honesty rules (frontend/CLAUDE.md section 3) applied here:
 *
 *   * Synthetic history is labelled on the chart itself, not only by the page
 *     banner, because a chart is exactly the kind of thing that gets
 *     screenshotted away from its banner.
 *   * A timeframe Dhan does not serve natively says so -- those bars were
 *     aggregated by us from a finer interval.
 *   * A stale feed stops extending the forming candle. Painting the last known
 *     price into new buckets would draw trades that never happened.
 *
 * Charting library: TradingView Lightweight Charts (Apache-2.0). Its licence
 * requires attribution and a link to tradingview.com; `attributionLogo` is left
 * on deliberately and the NOTICE text is repeated under the chart. See
 * frontend/NOTICE.
 */
import { memo, useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
} from 'lightweight-charts';
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { chartsApi } from '../api/charts';
import { useFeedHealth, useMarketRow } from '../market/MarketFeedContext';
import { toChartTime } from '../utils/chartTime';

const CHART_HEIGHT = 420;
const SECONDS_PER_DAY = 86400;

function layoutOptions(theme) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: theme.palette.text.secondary,
      fontFamily: theme.typography.fontFamily,
      // Required by the Apache-2.0 attribution clause. Do not turn this off.
      attributionLogo: true,
    },
    grid: {
      vertLines: { color: theme.palette.divider },
      horzLines: { color: theme.palette.divider },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: theme.palette.divider },
    timeScale: { borderColor: theme.palette.divider },
    localization: { locale: 'en-IN' },
  };
}

function seriesOptions(theme) {
  return {
    upColor: theme.market.up,
    downColor: theme.market.down,
    wickUpColor: theme.market.up,
    wickDownColor: theme.market.down,
    borderVisible: false,
  };
}

/**
 * Feeds live ticks into the forming candle. Renders nothing on purpose: this
 * is the only part of the chart that re-renders at the feed's cadence.
 */
const LiveCandle = memo(function LiveCandle({ securityId, seriesRef, liveRef }) {
  const row = useMarketRow(securityId);
  const { stale, marketOpen } = useFeedHealth();
  const ltp = row?.ltp ?? null;

  useEffect(() => {
    const series = seriesRef.current;
    const live = liveRef.current;
    if (!series || !live || !live.last || ltp == null) return;
    // A stale book must not keep drawing bars. See the honesty rules above.
    if (stale) return;

    const price = Number(ltp);
    if (!Number.isFinite(price)) return;

    const nowSeconds = Math.floor(Date.now() / 1000);
    const elapsed = nowSeconds - live.last.epoch;
    const rollover = live.intraday && marketOpen && elapsed >= live.stepSeconds;

    if (rollover) {
      // Anchor the new bucket to the last bar rather than to the wall clock,
      // so the live grid always lines up with the history the server sent.
      const epoch = live.last.epoch + Math.floor(elapsed / live.stepSeconds) * live.stepSeconds;
      live.last = {
        epoch,
        open: live.last.close,
        high: Math.max(live.last.close, price),
        low: Math.min(live.last.close, price),
        close: price,
      };
    } else {
      live.last = {
        ...live.last,
        high: Math.max(live.last.high, price),
        low: Math.min(live.last.low, price),
        close: price,
      };
    }

    series.update({
      time: toChartTime(live.last.epoch),
      open: live.last.open,
      high: live.last.high,
      low: live.last.low,
      close: live.last.close,
    });
  }, [ltp, stale, marketOpen, seriesRef, liveRef]);

  return null;
});

export default function PriceChart({ securityId, title = 'Price chart', subtitle }) {
  const theme = useTheme();
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const liveRef = useRef(null);

  const [timeframes, setTimeframes] = useState([]);
  const [timeframe, setTimeframe] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [meta, setMeta] = useState(null);

  // --- the chart itself, created once ------------------------------------
  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      ...layoutOptions(theme),
    });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, seriesOptions(theme));
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
    // Created once; theme changes are applied by the effect below rather than
    // by tearing the chart down and losing the user's pan and zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions(layoutOptions(theme));
    seriesRef.current?.applyOptions(seriesOptions(theme));
  }, [theme]);

  // --- which timeframes this build actually serves ------------------------
  useEffect(() => {
    let cancelled = false;
    chartsApi
      .timeframes()
      .then((payload) => {
        if (cancelled) return;
        setTimeframes(payload.timeframes ?? []);
        setTimeframe((current) => current ?? payload.default ?? payload.timeframes?.[0]?.key);
      })
      .catch((apiError) => {
        if (!cancelled) setError(apiError.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // --- history ------------------------------------------------------------
  useEffect(() => {
    if (!securityId || !timeframe) return undefined;
    let cancelled = false;
    setLoading(true);
    setError(null);

    chartsApi
      .candles(securityId, timeframe)
      .then((payload) => {
        if (cancelled) return;
        const candles = payload.candles ?? [];
        seriesRef.current?.setData(
          candles.map((candle) => ({
            time: toChartTime(candle.time),
            open: candle.open,
            high: candle.high,
            low: candle.low,
            close: candle.close,
          })),
        );
        chartRef.current?.timeScale().fitContent();

        const newest = candles[candles.length - 1];
        liveRef.current = {
          stepSeconds: payload.stepSeconds,
          intraday: payload.stepSeconds < SECONDS_PER_DAY,
          last: newest
            ? {
                epoch: newest.time,
                open: newest.open,
                high: newest.high,
                low: newest.low,
                close: newest.close,
              }
            : null,
        };
        setMeta({
          synthetic: payload.synthetic,
          native: payload.native,
          count: candles.length,
          source: payload.source,
        });
        setLoading(false);
      })
      .catch((apiError) => {
        if (cancelled) return;
        seriesRef.current?.setData([]);
        liveRef.current = null;
        setMeta(null);
        setError(apiError.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [securityId, timeframe]);

  const selected = timeframes.find((option) => option.key === timeframe);
  const empty = !loading && !error && meta?.count === 0;

  // Where these bars came from, said plainly. A synthetic series must never be
  // described as Dhan data, and an aggregated one must not pass as native.
  const aggregated =
    selected && !selected.native
      ? ` ${selected.label} bars are aggregated from ${selected.derivedFrom} data — this interval is not served natively.`
      : '';
  const provenance = meta?.synthetic
    ? `These bars are generated locally and are not market data.${aggregated}`
    : `Bars come from Dhan; the newest one updates from the live feed.${aggregated}`;

  return (
    <Card>
      <CardContent sx={{ p: 3 }}>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          flexWrap="wrap"
          gap={2}
          sx={{ mb: 2 }}
        >
          <Box>
            <Typography variant="h4">{title}</Typography>
            {subtitle ? (
              <Typography variant="body2" color="text.secondary">
                {subtitle}
              </Typography>
            ) : null}
          </Box>

          <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
            {meta?.synthetic ? (
              <Chip
                size="small"
                color="warning"
                variant="outlined"
                label="SYNTHETIC — generated locally"
              />
            ) : null}
            <ToggleButtonGroup
              size="small"
              exclusive
              value={timeframe}
              onChange={(_event, value) => {
                if (value) setTimeframe(value);
              }}
            >
              {timeframes.map((option) => (
                <ToggleButton key={option.key} value={option.key} sx={{ px: 1.25 }}>
                  {option.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Stack>
        </Stack>

        <Box sx={{ position: 'relative', height: CHART_HEIGHT }}>
          <Box ref={containerRef} sx={{ position: 'absolute', inset: 0 }} />

          {loading ? (
            <Box
              sx={{
                position: 'absolute',
                inset: 0,
                display: 'grid',
                placeItems: 'center',
                bgcolor: 'background.paper',
                zIndex: 1,
              }}
            >
              <CircularProgress size={28} />
            </Box>
          ) : null}

          {error || empty ? (
            <Box
              sx={{
                position: 'absolute',
                inset: 0,
                display: 'grid',
                placeItems: 'center',
                bgcolor: 'background.paper',
                p: 3,
                zIndex: 1,
              }}
            >
              <Alert severity={error ? 'error' : 'info'} sx={{ maxWidth: 560 }}>
                {error ||
                  `Dhan returned no ${selected?.label ?? timeframe} candles for this contract. ` +
                    'A newly listed contract has no history yet.'}
              </Alert>
            </Box>
          ) : null}
        </Box>

        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          flexWrap="wrap"
          gap={1}
          sx={{ mt: 1.5 }}
        >
          <Typography variant="caption" color="text.secondary">
            {provenance}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Charts by{' '}
            <Link href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">
              TradingView
            </Link>
          </Typography>
        </Stack>
      </CardContent>

      <LiveCandle securityId={securityId} seriesRef={seriesRef} liveRef={liveRef} />
    </Card>
  );
}
