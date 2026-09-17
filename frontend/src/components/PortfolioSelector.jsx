import { useState } from 'react';
import {
  Box,
  Chip,
  Divider,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import AccountBalanceIcon from '@mui/icons-material/AccountBalance';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { useNavigate } from 'react-router-dom';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { formatPrice } from '../utils/format';

/**
 * The one picker that scopes the whole portal.
 *
 * It shows the AVAILABLE balance, not cash: available is what can actually be
 * spent, and the difference between the two is money blocked against open
 * shorts. Showing cash here would tell a trader they have money they cannot
 * use — which is exactly the confusion the four separate figures exist to
 * prevent.
 */
export default function PortfolioSelector() {
  const { portfolios, active, balance, select, loading } = useActivePortfolio();
  const [anchor, setAnchor] = useState(null);
  const navigate = useNavigate();

  if (loading) return null;

  if (!active) {
    return (
      <Tooltip title="An account admin creates one on the Portfolios page">
        <Chip
          size="small"
          color="warning"
          variant="outlined"
          icon={<AccountBalanceIcon sx={{ fontSize: 16 }} />}
          label="No portfolio"
          onClick={() => navigate('/portfolios')}
        />
      </Tooltip>
    );
  }

  const hasBlocked = Number(balance?.blockedMargin ?? 0) > 0;

  return (
    <>
      <Box
        onClick={(event) => setAnchor(event.currentTarget)}
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          px: 1,
          py: 0.5,
          borderRadius: 1,
          border: '1px solid',
          borderColor: 'divider',
          cursor: 'pointer',
          '&:hover': { borderColor: 'primary.main' },
        }}
      >
        <AccountBalanceIcon sx={{ fontSize: 17, color: 'text.secondary' }} />
        <Stack spacing={0} sx={{ lineHeight: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
            {active.name}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            className="numeric"
            sx={{ lineHeight: 1.2 }}
          >
            ₹{formatPrice(Number(balance?.available ?? 0))} available
          </Typography>
        </Stack>
        <ExpandMoreIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
      </Box>

      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        {portfolios.map((portfolio) => (
          <MenuItem
            key={portfolio.id}
            selected={portfolio.id === active.id}
            onClick={() => {
              select(portfolio.id);
              setAnchor(null);
            }}
            sx={{ minWidth: 300 }}
          >
            <ListItemText
              primary={portfolio.name}
              secondary={
                <Stack direction="row" spacing={1.5} component="span">
                  <span className="numeric">
                    ₹{formatPrice(Number(portfolio.balance?.available ?? 0))} available
                  </span>
                  {Number(portfolio.balance?.blockedMargin ?? 0) > 0 ? (
                    <span className="numeric">
                      ₹{formatPrice(Number(portfolio.balance.blockedMargin))} blocked
                      (est.)
                    </span>
                  ) : null}
                </Stack>
              }
              primaryTypographyProps={{ fontSize: '0.875rem', fontWeight: 500 }}
              secondaryTypographyProps={{ fontSize: '0.75rem', component: 'span' }}
            />
          </MenuItem>
        ))}
        <Divider />
        {hasBlocked ? (
          <Box sx={{ px: 2, py: 1, maxWidth: 320 }}>
            <Typography variant="caption" color="text.secondary">
              Blocked margin is an <strong>estimate</strong>, not what a broker
              would hold. See the Portfolios page.
            </Typography>
          </Box>
        ) : null}
        <MenuItem
          onClick={() => {
            setAnchor(null);
            navigate('/portfolios');
          }}
        >
          <ListItemText
            primary="Manage portfolios"
            primaryTypographyProps={{ fontSize: '0.875rem' }}
          />
        </MenuItem>
      </Menu>
    </>
  );
}
