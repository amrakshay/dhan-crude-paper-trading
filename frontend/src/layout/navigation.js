import ShowChartIcon from '@mui/icons-material/ShowChart';
import TableChartIcon from '@mui/icons-material/TableChart';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import AssessmentIcon from '@mui/icons-material/Assessment';
import StickyNote2Icon from '@mui/icons-material/StickyNote2';

export const navigationItems = [
  { path: '/live', label: 'Live Price', icon: ShowChartIcon },
  { path: '/chain', label: 'Option Chain', icon: TableChartIcon },
  { path: '/positions', label: 'Positions', icon: AccountBalanceWalletIcon },
  { path: '/orders', label: 'Order History', icon: ReceiptLongIcon },
  { path: '/reports', label: 'P&L Reports', icon: AssessmentIcon },
  { path: '/notes', label: 'Trade Notes', icon: StickyNote2Icon },
];
