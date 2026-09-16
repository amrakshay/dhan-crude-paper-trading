import { useCallback, useEffect, useMemo, useState } from 'react';
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
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import { LineChart } from '@mui/x-charts/LineChart';
import { useTheme } from '@mui/material/styles';
import { reportsApi } from '../api/reports';
import SyntheticBanner from '../components/SyntheticBanner';
import { formatPrice, formatQty } from '../utils/format';

function Money({ value, bold = false }) {
  const theme = useTheme();
  if (value === null || value === undefined) {
    return <Typography variant="body2" color="text.disabled">—</Typography>;
  }
  const numeric = Number(value);
  return (
    <Typography
      variant="body2"
      className="numeric"
      sx={{
        color: numeric >= 0 ? theme.market.up : theme.market.down,
        fontWeight: bold ? 600 : 400,
      }}
    >
      {numeric >= 0 ? '+' : ''}
      {formatPrice(numeric)}
    </Typography>
  );
}

function SummaryCard({ label, value, hint, color }) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack spacing={0.5}>
          <Tooltip title={hint ?? ''}>
            <Typography variant="caption" color="text.secondary">
              {label}
            </Typography>
          </Tooltip>
          <Typography variant="h4" className="numeric" sx={{ color }}>
            {value}
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  );
}

function BucketTable({ rows, keyLabel }) {
  if (rows.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
        Nothing realised yet in this breakdown.
      </Typography>
    );
  }
  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>{keyLabel}</TableCell>
            <TableCell align="right">Gross P&L</TableCell>
            <TableCell align="right">Trades</TableCell>
            <TableCell align="right">Wins</TableCell>
            <TableCell align="right">Losses</TableCell>
            <TableCell align="right">Quantity</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.key} className="numeric">
              <TableCell>{row.key}</TableCell>
              <TableCell align="right">
                <Money value={row.grossPnl} />
              </TableCell>
              <TableCell align="right">{row.trades}</TableCell>
              <TableCell align="right">{row.wins}</TableCell>
              <TableCell align="right">{row.losses}</TableCell>
              <TableCell align="right">{formatQty(row.quantity)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

const CHARGE_LABELS = {
  brokerage: 'Brokerage',
  ctt: 'CTT',
  exchangeTransactionCharge: 'Exchange txn',
  sebiTurnoverFee: 'SEBI fee',
  stampDuty: 'Stamp duty',
  gst: 'GST',
};

export default function ReportsPage() {
  const theme = useTheme();
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState(0);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');

  const params = useMemo(() => {
    const value = {};
    if (from) value.from = `${from}T00:00:00`;
    if (to) value.to = `${to}T23:59:59`;
    return value;
  }, [from, to]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await reportsApi.pnl(params));
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [params]);

  useEffect(() => {
    load();
  }, [load]);

  const curve = report?.equityCurve ?? [];
  const hasCurve = curve.length > 0;

  const chargeRows = Object.entries(report?.chargeComponents ?? {});
  const chargeTotal = Number(report?.totalCharges ?? 0);

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h2">P&amp;L Reports</Typography>
          <Typography variant="body2" color="text.secondary">
            Realised and unrealised, gross vs net of charges
          </Typography>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap">
          <TextField
            size="small"
            type="date"
            label="From"
            InputLabelProps={{ shrink: true }}
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
          <TextField
            size="small"
            type="date"
            label="To"
            InputLabelProps={{ shrink: true }}
            value={to}
            onChange={(event) => setTo(event.target.value)}
          />
          <Button
            startIcon={<DownloadIcon />}
            variant="outlined"
            component="a"
            href={reportsApi.pnlCsvUrl(params)}
          >
            Export CSV
          </Button>
        </Stack>
      </Stack>

      <SyntheticBanner />
      {error ? <Alert severity="error">{error}</Alert> : null}

      {loading && !report ? (
        <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 200 }}>
          <CircularProgress />
        </Box>
      ) : report ? (
        <>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={3}>
              <SummaryCard
                label="Realised (gross)"
                value={formatPrice(Number(report.realisedGross))}
                color={Number(report.realisedGross) >= 0 ? theme.market.up : theme.market.down}
                hint="Before any charges"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <SummaryCard
                label="Total charges"
                value={formatPrice(chargeTotal)}
                color={theme.market.down}
                hint="Brokerage, CTT, exchange, SEBI, stamp duty and GST"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <SummaryCard
                label="Realised (net)"
                value={formatPrice(Number(report.realisedNet))}
                color={Number(report.realisedNet) >= 0 ? theme.market.up : theme.market.down}
                hint="Gross realised minus charges — the number that matters"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <SummaryCard
                label="Incl. unrealised"
                value={
                  report.netIncludingUnrealised === null
                    ? '—'
                    : formatPrice(Number(report.netIncludingUnrealised))
                }
                color={
                  report.netIncludingUnrealised === null
                    ? 'text.disabled'
                    : Number(report.netIncludingUnrealised) >= 0
                      ? theme.market.up
                      : theme.market.down
                }
                hint="Net realised plus mark-to-market on open positions"
              />
            </Grid>
          </Grid>

          <Stack direction="row" spacing={1} flexWrap="wrap">
            <Chip size="small" variant="outlined" label={`${report.tradeCount} realisations`} />
            <Chip size="small" variant="outlined" label={`${report.winCount} wins`} />
            <Chip size="small" variant="outlined" label={`${report.lossCount} losses`} />
            {report.unrealised !== null ? (
              <Chip
                size="small"
                variant="outlined"
                label={`unrealised ${formatPrice(Number(report.unrealised))}`}
              />
            ) : null}
          </Stack>

          {report.openPositionsWithoutMarks > 0 ? (
            <Alert severity="warning">
              {report.openPositionsWithoutMarks} open position(s) have no live price, so their
              mark-to-market is excluded from the unrealised figure above.
            </Alert>
          ) : null}

          <Card>
            <CardContent>
              <Typography variant="h4" sx={{ mb: 1 }}>
                Equity curve
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Cumulative realised P&amp;L by day. The net line has charges applied on the day
                they were incurred, so it steps down even on days that only opened positions.
              </Typography>
              {hasCurve ? (
                <Box sx={{ mt: 2 }}>
                  <LineChart
                    height={300}
                    xAxis={[{ scaleType: 'point', data: curve.map((point) => point.date) }]}
                    series={[
                      {
                        data: curve.map((point) => Number(point.cumulativeGross)),
                        label: 'Cumulative gross',
                        color: theme.palette.primary.main,
                        showMark: curve.length < 40,
                      },
                      {
                        data: curve.map((point) => Number(point.cumulativeNet)),
                        label: 'Cumulative net of charges',
                        color: theme.market.down,
                        showMark: curve.length < 40,
                      },
                    ]}
                    margin={{ left: 70, right: 20, top: 30, bottom: 30 }}
                  />
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                  No realised trades yet — the curve appears once a position is closed.
                </Typography>
              )}
            </CardContent>
          </Card>

          <Grid container spacing={3}>
            <Grid item xs={12} md={5}>
              <Card sx={{ height: '100%' }}>
                <CardContent>
                  <Typography variant="h4" sx={{ mb: 2 }}>
                    Charges by component
                  </Typography>
                  <Stack spacing={1}>
                    {chargeRows.map(([key, value]) => {
                      const amount = Number(value);
                      const share = chargeTotal > 0 ? (amount / chargeTotal) * 100 : 0;
                      return (
                        <Box key={key}>
                          <Stack direction="row" justifyContent="space-between">
                            <Typography variant="body2" color="text.secondary">
                              {CHARGE_LABELS[key] ?? key}
                            </Typography>
                            <Typography variant="body2" className="numeric">
                              {formatPrice(amount)}
                              <Typography component="span" variant="caption" color="text.disabled">
                                {' '}
                                ({share.toFixed(0)}%)
                              </Typography>
                            </Typography>
                          </Stack>
                          <Box
                            sx={{
                              mt: 0.5,
                              height: 4,
                              borderRadius: 2,
                              bgcolor: 'action.hover',
                              overflow: 'hidden',
                            }}
                          >
                            <Box
                              sx={{
                                width: `${share}%`,
                                height: '100%',
                                bgcolor: 'primary.main',
                              }}
                            />
                          </Box>
                        </Box>
                      );
                    })}
                    <Divider sx={{ my: 1 }} />
                    <Stack direction="row" justifyContent="space-between">
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        Total
                      </Typography>
                      <Typography variant="body2" className="numeric" sx={{ fontWeight: 600 }}>
                        {formatPrice(chargeTotal)}
                      </Typography>
                    </Stack>
                  </Stack>
                </CardContent>
              </Card>
            </Grid>

            <Grid item xs={12} md={7}>
              <Card sx={{ height: '100%' }}>
                <Tabs value={tab} onChange={(_event, value) => setTab(value)} sx={{ px: 2 }}>
                  <Tab label="By day" />
                  <Tab label="By expiry" />
                  <Tab label="By strike" />
                </Tabs>
                <Divider />
                {tab === 0 ? <BucketTable rows={report.byDay} keyLabel="Date (IST)" /> : null}
                {tab === 1 ? <BucketTable rows={report.byExpiry} keyLabel="Expiry" /> : null}
                {tab === 2 ? <BucketTable rows={report.byStrike} keyLabel="Strike" /> : null}
              </Card>
            </Grid>
          </Grid>
        </>
      ) : null}
    </Stack>
  );
}
