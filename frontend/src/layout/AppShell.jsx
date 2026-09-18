import { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  AppBar,
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
} from '@mui/material';
import LightModeIcon from '@mui/icons-material/LightMode';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import LogoutIcon from '@mui/icons-material/Logout';
import PersonIcon from '@mui/icons-material/Person';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import { useColorMode } from '../theme/ColorModeContext';
import { useAuth } from '../auth/AuthContext';
import { visibleNavigationItems } from './navigation';
import FeedStatusIndicator from '../components/FeedStatusIndicator';
import { MarketFeedProvider } from '../market/MarketFeedContext';
import { ActivePortfolioProvider } from '../portfolios/ActivePortfolioContext';
import PortfolioSelector from '../components/PortfolioSelector';

function AppShellInner() {
  const { mode, toggle } = useColorMode();
  const { session, logout, pages, isAdmin } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [accountMenu, setAccountMenu] = useState(null);

  // Filtered from the server's role -> pages mapping. Presentation only: the
  // API refuses what this role may not do, whatever the sidebar shows.
  const items = visibleNavigationItems(pages);

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: 'background.default' }}>
      <AppBar position="fixed" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
        <Toolbar sx={{ minHeight: 57, gap: 2 }}>
          <Box
            sx={{
              width: 30,
              height: 30,
              borderRadius: 1.5,
              display: 'grid',
              placeItems: 'center',
              bgcolor: 'primary.main',
              color: 'primary.contrastText',
            }}
          >
            <ShowChartIcon sx={{ fontSize: 18 }} />
          </Box>
          <Typography variant="h5" sx={{ fontWeight: 600 }}>
            Paper Trading
          </Typography>

          <Box sx={{ flexGrow: 1 }} />

          <PortfolioSelector />

          <FeedStatusIndicator />

          <Tooltip title={mode === 'light' ? 'Switch to dark' : 'Switch to light'}>
            <IconButton onClick={toggle} size="small">
              {mode === 'light' ? <DarkModeIcon /> : <LightModeIcon />}
            </IconButton>
          </Tooltip>

          {/* The account menu. Profile is no longer in the sidebar -- a page
              about YOU does not belong in a rail of pages about trading -- so
              this is the only way to reach it and it has to look like a menu
              rather than a name that happens to be clickable. Sign out moved
              in with it: two ways to do one thing, a menu item and a stray
              icon, is how a header accumulates. */}
          <Button
            onClick={(event) => setAccountMenu(event.currentTarget)}
            size="small"
            endIcon={<ExpandMoreIcon />}
            startIcon={<PersonIcon sx={{ fontSize: 18 }} />}
            sx={{ color: 'text.secondary', textTransform: 'none' }}
          >
            <Typography variant="body2" color="inherit" noWrap>
              {session?.fullName || session?.email}
            </Typography>
          </Button>
          <Menu
            anchorEl={accountMenu}
            open={Boolean(accountMenu)}
            onClose={() => setAccountMenu(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
            transformOrigin={{ vertical: 'top', horizontal: 'right' }}
          >
            <Box sx={{ px: 2, py: 1 }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {session?.fullName || session?.email}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {session?.email} — {isAdmin ? 'Account admin' : 'User'}
              </Typography>
            </Box>
            <Divider />
            {/* Gated the same way every other page is: from the server's own
                page list, not from a role check written here (§5). */}
            {pages?.includes('/profile') ? (
              <MenuItem
                onClick={() => {
                  setAccountMenu(null);
                  navigate('/profile');
                }}
              >
                <ListItemIcon>
                  <PersonIcon fontSize="small" />
                </ListItemIcon>
                <ListItemText>Profile</ListItemText>
              </MenuItem>
            ) : null}
            <MenuItem
              onClick={() => {
                setAccountMenu(null);
                handleLogout();
              }}
            >
              <ListItemIcon>
                <LogoutIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText>Sign out</ListItemText>
            </MenuItem>
          </Menu>
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: 232,
          flexShrink: 0,
          '& .MuiDrawer-paper': { width: 232, boxSizing: 'border-box' },
        }}
      >
        <Toolbar sx={{ minHeight: 57 }} />
        <Box sx={{ p: 1.5, overflow: 'auto' }}>
          <List sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
            {items.map((item) => {
              const Icon = item.icon;
              const selected = location.pathname.startsWith(item.path);
              return (
                <ListItem key={item.path} disablePadding>
                  <ListItemButton
                    selected={selected}
                    onClick={() => navigate(item.path)}
                    sx={{ py: 1 }}
                  >
                    <ListItemIcon sx={{ minWidth: 36, color: 'inherit' }}>
                      <Icon sx={{ fontSize: 20 }} />
                    </ListItemIcon>
                    <ListItemText
                      primary={item.label}
                      primaryTypographyProps={{ fontSize: '0.875rem', fontWeight: 500 }}
                    />
                  </ListItemButton>
                </ListItem>
              );
            })}
          </List>
          <Divider sx={{ my: 2 }} />
          <Typography variant="caption" color="text.secondary" sx={{ px: 1.5 }}>
            Paper rupees only. No order placed by this tool ever reaches a broker.
          </Typography>
        </Box>
      </Drawer>

      <Box component="main" sx={{ flexGrow: 1, minWidth: 0 }}>
        <Toolbar sx={{ minHeight: 57 }} />
        <Box sx={{ p: 3 }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}

/**
 * The market feed socket is opened once, around the whole authenticated shell,
 * so navigating between pages never tears it down and reconnects.
 *
 * The active portfolio is a SEPARATE provider, deliberately outside the feed
 * context rather than inside it: the feed context has a performance contract
 * (rows in a ref, one version bump per batch) and a portfolio change must not
 * re-render every subscriber to it (frontend/CLAUDE.md section 2).
 */
export default function AppShell() {
  return (
    <ActivePortfolioProvider>
      <MarketFeedProvider>
        <AppShellInner />
      </MarketFeedProvider>
    </ActivePortfolioProvider>
  );
}
