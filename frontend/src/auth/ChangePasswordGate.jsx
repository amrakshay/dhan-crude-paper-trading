import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import LockResetIcon from '@mui/icons-material/LockReset';
import { usersApi } from '../api/users';
import { useAuth } from './AuthContext';

/**
 * Shown INSTEAD of the app when a user still owes a password change.
 *
 * Not a route the user can navigate away from: an admin created this account
 * with a temporary password, and the server refuses every other endpoint with
 * a 403 until it is changed. This screen is the UI half of that rule -- the
 * enforcing half is `require_session` in the backend.
 */
export default function ChangePasswordGate() {
  const { session, logout, refresh } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError(null);
    if (newPassword !== confirmPassword) {
      setError('The two new passwords do not match.');
      return;
    }
    setSubmitting(true);
    try {
      await usersApi.changePassword(currentPassword, newPassword);
      // Clears mustChangePassword on the session, which releases the gate.
      await refresh();
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        bgcolor: 'background.default',
        p: 2,
      }}
    >
      <Card sx={{ width: '100%', maxWidth: 440 }}>
        <CardContent sx={{ p: 4 }}>
          <Stack spacing={3} component="form" onSubmit={handleSubmit}>
            <Stack spacing={1.5} alignItems="center" textAlign="center">
              <Box
                sx={{
                  width: 48,
                  height: 48,
                  borderRadius: 2,
                  display: 'grid',
                  placeItems: 'center',
                  bgcolor: 'warning.main',
                  color: 'warning.contrastText',
                }}
              >
                <LockResetIcon />
              </Box>
              <Typography variant="h3">Set your password</Typography>
              <Typography variant="body2" color="text.secondary">
                Your account was created with a temporary password. Choose your own
                before continuing.
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Signed in as {session?.email}
              </Typography>
            </Stack>

            {error ? <Alert severity="error">{error}</Alert> : null}

            <TextField
              label="Temporary password"
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
              autoFocus
              required
              fullWidth
            />
            <TextField
              label="New password"
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
              helperText="At least 8 characters, and at most 72 bytes."
              required
              fullWidth
            />
            <TextField
              label="Confirm new password"
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              autoComplete="new-password"
              error={Boolean(confirmPassword) && confirmPassword !== newPassword}
              required
              fullWidth
            />

            <Button
              type="submit"
              variant="contained"
              size="large"
              disabled={
                submitting || !currentPassword || !newPassword || !confirmPassword
              }
            >
              {submitting ? 'Saving…' : 'Set password and continue'}
            </Button>
            <Button size="small" onClick={logout}>
              Sign out instead
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
