import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { ROLE_LABELS, STATUS_LABELS, usersApi } from '../api/users';
import { useAuth } from '../auth/AuthContext';

export default function ProfilePage() {
  const { refresh: refreshSession } = useAuth();
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [detailsError, setDetailsError] = useState(null);
  const [detailsNotice, setDetailsNotice] = useState(null);
  const [savingDetails, setSavingDetails] = useState(false);

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState(null);
  const [passwordNotice, setPasswordNotice] = useState(null);
  const [savingPassword, setSavingPassword] = useState(false);

  const load = useCallback(async () => {
    try {
      const me = await usersApi.me();
      setUser(me);
      setFirstName(me.firstName);
      setLastName(me.lastName);
    } catch (error) {
      setDetailsError(error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const saveDetails = async (event) => {
    event.preventDefault();
    setSavingDetails(true);
    setDetailsError(null);
    setDetailsNotice(null);
    try {
      // `email` is not sent: it can never change, and the server rejects a
      // different one outright.
      const updated = await usersApi.updateProfile({ firstName, lastName });
      setUser(updated);
      setDetailsNotice('Your details were saved.');
      await refreshSession();
    } catch (error) {
      setDetailsError(error.message);
    } finally {
      setSavingDetails(false);
    }
  };

  const savePassword = async (event) => {
    event.preventDefault();
    setPasswordError(null);
    setPasswordNotice(null);
    if (newPassword !== confirmPassword) {
      setPasswordError('The two new passwords do not match.');
      return;
    }
    setSavingPassword(true);
    try {
      await usersApi.changePassword(currentPassword, newPassword);
      setPasswordNotice('Your password was changed.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      await refreshSession();
    } catch (error) {
      setPasswordError(error.message);
    } finally {
      setSavingPassword(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Stack spacing={3} sx={{ maxWidth: 680 }}>
      <Box>
        <Typography variant="h2">Profile</Typography>
        <Typography variant="body2" color="text.secondary">
          Your own details and password. Role and status are set by an account admin.
        </Typography>
      </Box>

      <Card>
        <CardContent>
          <Stack spacing={2} component="form" onSubmit={saveDetails}>
            <Typography variant="h6">Your details</Typography>

            {detailsError ? <Alert severity="error">{detailsError}</Alert> : null}
            {detailsNotice ? <Alert severity="success">{detailsNotice}</Alert> : null}

            <TextField
              label="Email"
              value={user?.email ?? ''}
              disabled
              helperText="Email is the login identifier and can never be changed."
              fullWidth
            />
            <Stack direction="row" spacing={2}>
              <TextField
                label="First name"
                value={firstName}
                onChange={(event) => setFirstName(event.target.value)}
                required
                fullWidth
              />
              <TextField
                label="Last name"
                value={lastName}
                onChange={(event) => setLastName(event.target.value)}
                required
                fullWidth
              />
            </Stack>

            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2" color="text.secondary">Role</Typography>
              <Chip size="small" label={ROLE_LABELS[user?.role] ?? user?.role} />
              <Typography variant="body2" color="text.secondary" sx={{ pl: 2 }}>
                Status
              </Typography>
              <Chip
                size="small"
                variant="outlined"
                label={STATUS_LABELS[user?.status] ?? user?.status}
              />
            </Stack>

            <Box>
              <Button
                type="submit"
                variant="contained"
                disabled={savingDetails || !firstName || !lastName}
              >
                {savingDetails ? 'Saving…' : 'Save details'}
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack spacing={2} component="form" onSubmit={savePassword}>
            <Typography variant="h6">Change password</Typography>
            <Divider />

            {passwordError ? <Alert severity="error">{passwordError}</Alert> : null}
            {passwordNotice ? <Alert severity="success">{passwordNotice}</Alert> : null}

            <TextField
              label="Current password"
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
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
            <Box>
              <Button
                type="submit"
                variant="contained"
                disabled={
                  savingPassword || !currentPassword || !newPassword || !confirmPassword
                }
              >
                {savingPassword ? 'Changing…' : 'Change password'}
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>
    </Stack>
  );
}
