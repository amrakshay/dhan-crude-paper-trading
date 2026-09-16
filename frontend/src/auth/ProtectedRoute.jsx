import { Navigate, useLocation } from 'react-router-dom';
import { Box, CircularProgress } from '@mui/material';
import { useAuth } from './AuthContext';
import ChangePasswordGate from './ChangePasswordGate';

export default function ProtectedRoute({ children }) {
  const { session, loading, mustChangePassword } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
        <CircularProgress />
      </Box>
    );
  }
  if (!session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  // An account created with a temporary password reaches nothing else until it
  // is changed. Rendered INSTEAD of the shell rather than as a route, so there
  // is nowhere to navigate around it. The server enforces the same rule with a
  // 403 on every other endpoint -- this is only the UI half.
  if (mustChangePassword) {
    return <ChangePasswordGate />;
  }
  return children;
}
