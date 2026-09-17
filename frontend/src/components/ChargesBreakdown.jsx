import { Box, Divider, Stack, Tooltip, Typography } from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { formatPrice } from '../utils/format';

/**
 * Itemised charges, driven by the components the server sends.
 *
 * The line items are NOT listed here. Which charges exist is a property of the
 * rate card an order was charged under — MCX commodity options pay CTT, an NSE
 * equity strategy would pay STT — so the server sends {name, label, amount,
 * note} per line and this renders whatever it is given. A hardcoded list would
 * silently drop a tax the moment a second rate card appeared.
 *
 * Each line's tooltip is the `note` from the rate card, which carries the
 * statute or circular the number comes from.
 */

/** Fallbacks for line items whose card predates the label field. */
const FALLBACK_LABELS = {
  brokerage: 'Brokerage',
  ctt: 'CTT',
  ctt_exercise: 'CTT (exercise)',
  exchange_transaction_charge: 'Exchange txn charge',
  sebi_turnover_fee: 'SEBI turnover fee',
  sebi_turnover_fee_exercise: 'SEBI fee (exercise)',
  stamp_duty: 'Stamp duty',
  gst: 'GST',
};

function labelFor(component) {
  if (component.label) return component.label;
  if (FALLBACK_LABELS[component.name]) return FALLBACK_LABELS[component.name];
  const words = String(component.name || '').replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Charge';
}

export default function ChargesBreakdown({ charges, dense = false }) {
  if (!charges) return null;

  const components = Array.isArray(charges.components) ? charges.components : [];
  const total = Number(charges.total ?? charges.totalCharges ?? 0);

  return (
    <Stack spacing={dense ? 0.25 : 0.5}>
      {components.map((component) => (
        <Stack
          key={component.name}
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="body2" color="text.secondary">
              {labelFor(component)}
            </Typography>
            {component.note ? (
              <Tooltip title={component.note}>
                <InfoOutlinedIcon sx={{ fontSize: 13, color: 'text.disabled' }} />
              </Tooltip>
            ) : null}
          </Stack>
          <Typography variant="body2" className="numeric">
            {formatPrice(Number(component.amount ?? 0))}
          </Typography>
        </Stack>
      ))}
      {components.length === 0 ? (
        <Typography variant="caption" color="text.disabled">
          No itemised breakdown stored for this order.
        </Typography>
      ) : null}
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
