import { useMemo } from 'react';
import { Box, Stack, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { formatQty, formatSignedPrice } from '../utils/format';

/**
 * Payoff AT EXPIRY of a set of option legs, as inline SVG in theme tokens
 * (frontend/CLAUDE.md s.5d: static pictures do not justify a chart library).
 *
 * Each leg: { optionType: 'CE'|'PE', strike, quantity (signed units), price }.
 * Pure arithmetic on intrinsic value; no volatility, no time value -- it is
 * the picture of the expiry, and it says so.
 */
const WIDTH = 400;
const HEIGHT = 170;
const PAD = 14;

function payoffAt(legs, underlying) {
  return legs.reduce((total, leg) => {
    const intrinsic =
      leg.optionType === 'CE'
        ? Math.max(underlying - leg.strike, 0)
        : Math.max(leg.strike - underlying, 0);
    return total + (intrinsic - (leg.price ?? 0)) * leg.quantity;
  }, 0);
}

export default function PayoffChart({ legs, spot }) {
  const theme = useTheme();
  const model = useMemo(() => {
    const priced = legs.filter((leg) => leg.price !== null && leg.price !== undefined);
    if (!priced.length || !spot) return null;
    const low = spot * 0.92;
    const high = spot * 1.08;
    const points = [];
    for (let i = 0; i <= 160; i += 1) {
      const x = low + ((high - low) * i) / 160;
      points.push([x, payoffAt(priced, x)]);
    }
    const values = points.map((point) => point[1]);
    const top = Math.max(...values, 1);
    const bottom = Math.min(...values, -1);
    const breakevens = [];
    for (let i = 1; i < points.length; i += 1) {
      if (Math.sign(points[i][1]) !== Math.sign(points[i - 1][1])) breakevens.push(points[i][0]);
    }
    // Beyond the drawn range: does the line keep rising or falling?
    const farUp = payoffAt(priced, spot * 3) - payoffAt(priced, spot * 2.9);
    const maxProfit = farUp > 0.5 ? null : Math.max(...values, payoffAt(priced, spot * 0.01));
    const maxLoss = farUp < -0.5 ? null : Math.min(...values, payoffAt(priced, spot * 0.01));
    return { low, high, points, top, bottom, breakevens, maxProfit, maxLoss, partial: priced.length < legs.length };
  }, [legs, spot]);

  if (!model) {
    return (
      <Typography variant="body2" color="text.secondary">
        The payoff appears once every leg has a price.
      </Typography>
    );
  }

  const x = (value) => PAD + ((value - model.low) / (model.high - model.low)) * (WIDTH - 2 * PAD);
  const y = (value) => PAD + ((model.top - value) / (model.top - model.bottom)) * (HEIGHT - 2 * PAD);
  const zero = y(0);
  const line = model.points.map(([px, py], i) => `${i ? 'L' : 'M'}${x(px).toFixed(1)},${y(py).toFixed(1)}`).join('');
  const area = `${line}L${x(model.high)},${zero}L${x(model.low)},${zero}Z`;

  return (
    <Stack spacing={1}>
      <Box
        component="svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        sx={{ width: '100%', height: HEIGHT, border: 1, borderColor: 'divider', borderRadius: 1, bgcolor: 'background.default' }}
      >
        <defs>
          <clipPath id="payoff-up"><rect x="0" y="0" width={WIDTH} height={zero} /></clipPath>
          <clipPath id="payoff-down"><rect x="0" y={zero} width={WIDTH} height={HEIGHT - zero} /></clipPath>
        </defs>
        <path d={area} fill={theme.market.upSoft} clipPath="url(#payoff-up)" />
        <path d={area} fill={theme.market.downSoft} clipPath="url(#payoff-down)" />
        <line x1="0" x2={WIDTH} y1={zero} y2={zero} stroke={theme.palette.divider} />
        <line x1={x(spot)} x2={x(spot)} y1="0" y2={HEIGHT} stroke={theme.palette.primary.main} strokeDasharray="3 3" />
        <text x={x(spot) + 4} y="12" fontSize="10" fill={theme.palette.primary.main}>spot {formatQty(Math.round(spot))}</text>
        <path d={line} fill="none" stroke={theme.palette.text.primary} strokeWidth="1.6" />
        <text x={PAD} y={HEIGHT - 3} fontSize="10" fill={theme.palette.text.secondary}>{formatQty(Math.round(model.low))}</text>
        <text x={WIDTH - PAD} y={HEIGHT - 3} fontSize="10" textAnchor="end" fill={theme.palette.text.secondary}>{formatQty(Math.round(model.high))}</text>
      </Box>
      <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap className="numeric">
        <Typography variant="caption">
          Max profit:{' '}
          <Box component="span" sx={{ color: theme.market.up, fontWeight: 600 }}>
            {model.maxProfit === null ? 'Unlimited' : formatSignedPrice(model.maxProfit)}
          </Box>
        </Typography>
        <Typography variant="caption">
          Max loss:{' '}
          <Box component="span" sx={{ color: theme.market.down, fontWeight: 600 }}>
            {model.maxLoss === null ? 'Unlimited' : formatSignedPrice(model.maxLoss)}
          </Box>
        </Typography>
        <Typography variant="caption">
          Breakeven{model.breakevens.length === 1 ? '' : 's'}:{' '}
          {model.breakevens.length ? model.breakevens.map((value) => formatQty(Math.round(value))).join(' · ') : '—'}
        </Typography>
      </Stack>
      <Typography variant="caption" color="text.secondary">
        At expiry, from the prices shown. {model.partial ? 'Legs without a price are left out.' : ''}
      </Typography>
    </Stack>
  );
}
