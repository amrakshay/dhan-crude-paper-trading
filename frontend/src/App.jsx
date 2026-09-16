import { Navigate, Route, Routes } from 'react-router-dom';
import AppShell from './layout/AppShell';
import ProtectedRoute from './auth/ProtectedRoute';
import LoginPage from './auth/LoginPage';
import LivePricePage from './pages/LivePricePage';
import OptionChainPage from './pages/OptionChainPage';
import PositionsPage from './pages/PositionsPage';
import OrderHistoryPage from './pages/OrderHistoryPage';
import ReportsPage from './pages/ReportsPage';
import NotesPage from './pages/NotesPage';

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
        <Route path="/live" element={<LivePricePage />} />
        <Route path="/chain" element={<OptionChainPage />} />
        <Route path="/positions" element={<PositionsPage />} />
        <Route path="/orders" element={<OrderHistoryPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/notes" element={<NotesPage />} />
        <Route path="/" element={<Navigate to="/live" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/live" replace />} />
    </Routes>
  );
}
