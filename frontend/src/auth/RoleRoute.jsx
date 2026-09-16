import { Navigate } from 'react-router-dom';
import { Alert, Stack, Typography } from '@mui/material';
import { useAuth } from './AuthContext';

/**
 * Gates a route on the role -> pages mapping from `conf/role-pages.json`.
 *
 * This is navigation convenience, NOT access control. The API enforces the
 * same rules on every endpoint -- a ROLE_USER calling the settings endpoints
 * directly gets a 403 whatever this component decides.
 */
export default function RoleRoute({ path, children }) {
  const { pages, canSee, session } = useAuth();

  // Before the mapping has loaded, render rather than bounce: the session is
  // already known to be valid, and flashing a redirect would be worse.
  if (!session || pages.length === 0) return children;

  if (!canSee(path)) {
    // Somewhere they can actually go, rather than a dead end.
    const fallback = pages.includes('/live') ? '/live' : pages[0];
    if (fallback && fallback !== path) {
      return <Navigate to={fallback} replace />;
    }
    return (
      <Stack spacing={2}>
        <Typography variant="h2">Not available</Typography>
        <Alert severity="info">
          Your role does not have access to this page. Ask an account admin if you
          need it.
        </Alert>
      </Stack>
    );
  }

  return children;
}
