import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  AppBar,
  Box,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
} from '@mui/material';
import LightModeIcon from '@mui/icons-material/LightMode';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LogoutIcon from '@mui/icons-material/Logout';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import { useColorMode } from '../theme/ColorModeContext';
import { useAuth } from '../auth/AuthContext';
import { navigationItems } from './navigation';
import FeedStatusIndicator from '../components/FeedStatusIndicator';
import { MarketFeedProvider } from '../market/MarketFeedContext';

function AppShellInner() {
  const { mode, toggle } = useColorMode();
  const { session, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

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
            Crude Paper Trading
          </Typography>

          <Box sx={{ flexGrow: 1 }} />

          <FeedStatusIndicator />

          <Tooltip title={mode === 'light' ? 'Switch to dark' : 'Switch to light'}>
            <IconButton onClick={toggle} size="small">
              {mode === 'light' ? <DarkModeIcon /> : <LightModeIcon />}
            </IconButton>
          </Tooltip>

          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography variant="body2" color="text.secondary">
              {session?.username}
            </Typography>
            <Tooltip title="Sign out">
              <IconButton onClick={handleLogout} size="small">
                <LogoutIcon />
              </IconButton>
            </Tooltip>
          </Stack>
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
            {navigationItems.map((item) => {
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
 */
export default function AppShell() {
  return (
    <MarketFeedProvider>
      <AppShellInner />
    </MarketFeedProvider>
  );
}
