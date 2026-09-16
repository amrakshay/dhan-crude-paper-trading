import { Box, Divider, Stack, Tooltip, Typography } from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { formatPrice } from '../utils/format';

/**
 * Itemised charges. Every line is named the way the statute or the exchange
 * names it, so a figure here can be traced to conf/charges.yaml.
 */
const LINES = [
  { key: 'brokerage', label: 'Brokerage', hint: 'Flat per executed order' },
  {
    key: 'ctt',
    label: 'CTT',
    hint: 'Commodities Transaction Tax (not STT) — 0.05% of premium, sell side only',
  },
  {
    key: 'exchangeTransactionCharge',
    label: 'Exchange txn charge',
    hint: 'MCX: Rs 41.80 per lakh of premium turnover, per side',
  },
  { key: 'sebiTurnoverFee', label: 'SEBI turnover fee', hint: 'Rs 10 per crore' },
  { key: 'stampDuty', label: 'Stamp duty', hint: '0.003% of premium, buy side only' },
  {
    key: 'gst',
    label: 'GST',
    hint: '18% of brokerage + exchange charge + SEBI fee. Not on trade value, CTT or stamp duty.',
  },
];

export default function ChargesBreakdown({ charges, dense = false }) {
  if (!charges) return null;

  const value = (key) => Number(charges[key] ?? charges[key.replace(/([A-Z])/g, '_$1').toLowerCase()] ?? 0);
  const total = Number(charges.total ?? charges.totalCharges ?? 0);

  return (
    <Stack spacing={dense ? 0.25 : 0.5}>
      {LINES.map((line) => (
        <Stack key={line.key} direction="row" justifyContent="space-between" alignItems="center">
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="body2" color="text.secondary">
              {line.label}
            </Typography>
            <Tooltip title={line.hint}>
              <InfoOutlinedIcon sx={{ fontSize: 13, color: 'text.disabled' }} />
            </Tooltip>
          </Stack>
          <Typography variant="body2" className="numeric">
            {formatPrice(value(line.key))}
          </Typography>
        </Stack>
      ))}
      <Divider sx={{ my: 0.5 }} />
      <Stack direction="row" justifyContent="space-between">
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Total charges
        </Typography>
        <Typography variant="body2" className="numeric" sx={{ fontWeight: 600 }}>
          {formatPrice(total)}
        </Typography>
      </Stack>
      {charges.ratesVersion ? (
        <Box sx={{ pt: 0.5 }}>
          <Typography variant="caption" color="text.disabled">
            Rate card {charges.ratesVersion}
            {charges.roundingMode ? ` · ${charges.roundingMode} rounding` : ''}
          </Typography>
        </Box>
      ) : null}
    </Stack>
  );
}
