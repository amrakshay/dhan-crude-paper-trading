import { Navigate, Route, Routes } from 'react-router-dom';
import AppShell from './layout/AppShell';
import ProtectedRoute from './auth/ProtectedRoute';
import RoleRoute from './auth/RoleRoute';
import LoginPage from './auth/LoginPage';
import LivePricePage from './pages/LivePricePage';
import OptionChainPage from './pages/OptionChainPage';
import PortfoliosPage from './pages/PortfoliosPage';
import PositionsPage from './pages/PositionsPage';
import OrderHistoryPage from './pages/OrderHistoryPage';
import ReportsPage from './pages/ReportsPage';
import NotesPage from './pages/NotesPage';
import UsersPage from './pages/UsersPage';
import ProfilePage from './pages/ProfilePage';
import SettingsPage from './pages/SettingsPage';
import ConnectionsPage from './pages/ConnectionsPage';
import StrategiesPage from './pages/StrategiesPage';
import SwingMomentumPage from './pages/SwingMomentumPage';
import BtstOvernightPage from './pages/BtstOvernightPage';
import SystemHealthPage from './pages/SystemHealthPage';

/**
 * `RoleRoute` checks the page against the role -> pages mapping the server
 * delivered with the session, so a ROLE_USER typing /settings lands somewhere
 * sensible instead of on a page whose every request 403s.
 *
 * That is convenience, not security: the API refuses those calls regardless of
 * what the router allows.
 */
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route path="/live" element={<RoleRoute path="/live"><LivePricePage /></RoleRoute>} />
        <Route path="/chain" element={<RoleRoute path="/chain"><OptionChainPage /></RoleRoute>} />
        <Route path="/positions" element={<RoleRoute path="/positions"><PositionsPage /></RoleRoute>} />
        <Route path="/orders" element={<RoleRoute path="/orders"><OrderHistoryPage /></RoleRoute>} />
        <Route path="/portfolios" element={<RoleRoute path="/portfolios"><PortfoliosPage /></RoleRoute>} />
        <Route path="/reports" element={<RoleRoute path="/reports"><ReportsPage /></RoleRoute>} />
        <Route path="/notes" element={<RoleRoute path="/notes"><NotesPage /></RoleRoute>} />
        <Route path="/users" element={<RoleRoute path="/users"><UsersPage /></RoleRoute>} />
        <Route path="/profile" element={<RoleRoute path="/profile"><ProfilePage /></RoleRoute>} />
        <Route path="/strategies" element={<RoleRoute path="/strategies"><StrategiesPage /></RoleRoute>} />
        <Route path="/swing" element={<RoleRoute path="/swing"><SwingMomentumPage /></RoleRoute>} />
        <Route path="/btst" element={<RoleRoute path="/btst"><BtstOvernightPage /></RoleRoute>} />
        <Route path="/connections" element={<RoleRoute path="/connections"><ConnectionsPage /></RoleRoute>} />
        <Route path="/settings" element={<RoleRoute path="/settings"><SettingsPage /></RoleRoute>} />
        <Route path="/health" element={<RoleRoute path="/health"><SystemHealthPage /></RoleRoute>} />
        {/* Positions, not the Crude Oil screen: that page is now gated
            behind the MCX strategy and is not there to land on when it is
            switched off. Positions is ungated and never withdrawn by a
            toggle, so it is always a valid destination. */}
        <Route path="/" element={<Navigate to="/positions" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/positions" replace />} />
    </Routes>
  );
}
