import ShowChartIcon from '@mui/icons-material/ShowChart';
import TableChartIcon from '@mui/icons-material/TableChart';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import AssessmentIcon from '@mui/icons-material/Assessment';
import StickyNote2Icon from '@mui/icons-material/StickyNote2';
import GroupIcon from '@mui/icons-material/Group';
import PersonIcon from '@mui/icons-material/Person';
import SettingsIcon from '@mui/icons-material/Settings';
import MonitorHeartIcon from '@mui/icons-material/MonitorHeart';
import AccountBalanceIcon from '@mui/icons-material/AccountBalance';
import TuneIcon from '@mui/icons-material/Tune';
import AutoModeIcon from '@mui/icons-material/AutoMode';

/**
 * Every page the app has. Which of them a given role actually sees is decided
 * on the server -- `conf/role-pages.json` intersected with the pages the
 * enabled strategies and capabilities grant -- and delivered with the session;
 * see AuthContext's `pages`. The paths here must match that file.
 *
 * That intersection is the ONLY gating mechanism. Do not add a second check
 * here for a disabled strategy: `pages` already reflects it.
 *
 * Filtering this list is presentation, not access control: the API refuses a
 * ROLE_USER calling the settings endpoints whatever the sidebar shows.
 */
export const navigationItems = [
  { path: '/live', label: 'Live Price', icon: ShowChartIcon },
  { path: '/chain', label: 'Option Chain', icon: TableChartIcon },
  { path: '/positions', label: 'Positions', icon: AccountBalanceWalletIcon },
  { path: '/orders', label: 'Order History', icon: ReceiptLongIcon },
  { path: '/portfolios', label: 'Portfolios', icon: AccountBalanceIcon },
  { path: '/reports', label: 'P&L Reports', icon: AssessmentIcon },
  { path: '/notes', label: 'Trade Notes', icon: StickyNote2Icon },
  { path: '/swing', label: 'Swing Momentum', icon: AutoModeIcon },
  { path: '/users', label: 'Users', icon: GroupIcon },
  { path: '/profile', label: 'Profile', icon: PersonIcon },
  { path: '/strategies', label: 'Strategies & Features', icon: TuneIcon },
  { path: '/settings', label: 'Settings', icon: SettingsIcon },
  { path: '/health', label: 'System Health', icon: MonitorHeartIcon },
];

/** The sidebar for a role, in the order declared above. */
export function visibleNavigationItems(pages) {
  if (!pages || pages.length === 0) return [];
  return navigationItems.filter((item) => pages.includes(item.path));
}
