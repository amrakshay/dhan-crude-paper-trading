import { Box, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { formatPrice, formatQty } from '../utils/format';

/**
 * Five-level market depth.
 *
 * Bars are sized against the largest quantity on screen so relative size is
 * readable at a glance, which is the only reason to show depth at all.
 */
export default function DepthLadder({ depth, dense = false }) {
  const theme = useTheme();
  if (!depth || depth.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No depth available.
      </Typography>
    );
  }

  const maxQty = Math.max(1, ...depth.flatMap(([bidQty, askQty]) => [bidQty, askQty]));

  return (
    <Table size="small" className="numeric">
      <TableHead>
        <TableRow>
          <TableCell align="right">Bid Qty</TableCell>
          <TableCell align="right">Bid</TableCell>
          <TableCell align="right">Ask</TableCell>
          <TableCell align="right">Ask Qty</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {depth.map(([bidQty, askQty, , , bidPrice, askPrice], index) => (
          <TableRow key={index} hover={false}>
            <TableCell align="right" sx={{ position: 'relative', py: dense ? 0.5 : 1 }}>
              <Box
                sx={{
                  position: 'absolute',
                  inset: 0,
                  right: 0,
                  width: `${(bidQty / maxQty) * 100}%`,
                  ml: 'auto',
                  bgcolor: theme.market.upSoft,
                }}
              />
              <Box sx={{ position: 'relative' }}>{formatQty(bidQty)}</Box>
            </TableCell>
            <TableCell align="right" sx={{ color: theme.market.up, fontWeight: 500 }}>
              {formatPrice(bidPrice)}
            </TableCell>
            <TableCell align="right" sx={{ color: theme.market.down, fontWeight: 500 }}>
              {formatPrice(askPrice)}
            </TableCell>
            <TableCell align="right" sx={{ position: 'relative', py: dense ? 0.5 : 1 }}>
              <Box
                sx={{
                  position: 'absolute',
                  inset: 0,
                  width: `${(askQty / maxQty) * 100}%`,
                  bgcolor: theme.market.downSoft,
                }}
              />
              <Box sx={{ position: 'relative' }}>{formatQty(askQty)}</Box>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
