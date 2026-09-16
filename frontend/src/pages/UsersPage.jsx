import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import LockResetIcon from '@mui/icons-material/LockReset';
import { ROLE_LABELS, STATUS_LABELS, usersApi } from '../api/users';
import { useAuth } from '../auth/AuthContext';

const EMPTY_FORM = {
  email: '',
  firstName: '',
  lastName: '',
  password: '',
  role: 'ROLE_USER',
  status: 'ACTIVE',
  mustChangePassword: true,
};

function RoleChip({ role }) {
  return (
    <Chip
      size="small"
      label={ROLE_LABELS[role] ?? role}
      color={role === 'ROLE_ACCOUNT_ADMIN' ? 'primary' : 'default'}
      variant={role === 'ROLE_ACCOUNT_ADMIN' ? 'filled' : 'outlined'}
    />
  );
}

function StatusChip({ status }) {
  const active = status === 'ACTIVE';
  return (
    <Chip
      size="small"
      label={STATUS_LABELS[status] ?? status}
      color={active ? 'success' : 'default'}
      variant="outlined"
    />
  );
}

export default function UsersPage() {
  const { isAdmin, session, refresh: refreshSession } = useAuth();
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const [editing, setEditing] = useState(null); // null | 'new' | user object
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, rolePages] = await Promise.all([
        usersApi.list(),
        usersApi.rolePages(),
      ]);
      setUsers(list.users);
      setRoles(rolePages.allRoles ?? []);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const roleOptions = useMemo(
    () =>
      roles.length > 0
        ? roles.map((entry) => ({ value: entry.role, label: entry.label }))
        : Object.entries(ROLE_LABELS).map(([value, label]) => ({ value, label })),
    [roles],
  );

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError(null);
    setEditing('new');
  };

  const openEdit = (user) => {
    setForm({
      email: user.email,
      firstName: user.firstName,
      lastName: user.lastName,
      password: '',
      role: user.role,
      status: user.status,
      mustChangePassword: user.mustChangePassword,
    });
    setFormError(null);
    setEditing(user);
  };

  const handleSave = async () => {
    setSaving(true);
    setFormError(null);
    try {
      if (editing === 'new') {
        await usersApi.create({
          email: form.email,
          firstName: form.firstName,
          lastName: form.lastName,
          password: form.password,
          role: form.role,
          status: form.status,
          mustChangePassword: form.mustChangePassword,
        });
        setNotice(`Created ${form.email}.`);
      } else {
        // `email` is deliberately not sent: it can never change, and the
        // server rejects a different one outright.
        const payload = {
          firstName: form.firstName,
          lastName: form.lastName,
          mustChangePassword: form.mustChangePassword,
        };
        if (!editing.isSeedUser) {
          payload.role = form.role;
          payload.status = form.status;
        }
        if (form.password) payload.password = form.password;
        await usersApi.update(editing.id, payload);
        setNotice(`Updated ${editing.email}.`);
        if (editing.id === session?.userId) await refreshSession();
      }
      setEditing(null);
      await load();
    } catch (saveError) {
      setFormError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    const target = confirmDelete;
    setConfirmDelete(null);
    try {
      await usersApi.remove(target.id);
      setNotice(`Deleted ${target.email}.`);
      await load();
    } catch (deleteError) {
      setError(deleteError.message);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
        <CircularProgress />
      </Box>
    );
  }

  const isNew = editing === 'new';
  const seedLocked = editing && editing !== 'new' && editing.isSeedUser;

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="h2">Users</Typography>
          <Typography variant="body2" color="text.secondary">
            {isAdmin
              ? 'Add, edit and remove accounts. Email can never be changed.'
              : 'Read-only. Only an account admin can add, edit or remove users.'}
          </Typography>
        </Box>
        {isAdmin ? (
          <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>
            Add user
          </Button>
        ) : null}
      </Stack>

      {error ? <Alert severity="error" onClose={() => setError(null)}>{error}</Alert> : null}
      {notice ? (
        <Alert severity="success" onClose={() => setNotice(null)}>{notice}</Alert>
      ) : null}

      <Card>
        <CardContent sx={{ p: 0 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Last login</TableCell>
                {isAdmin ? <TableCell align="right">Actions</TableCell> : null}
              </TableRow>
            </TableHead>
            <TableBody>
              {users.map((user) => (
                <TableRow key={user.id} hover>
                  <TableCell>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {user.fullName}
                      </Typography>
                      {user.id === session?.userId ? (
                        <Chip size="small" label="you" variant="outlined" />
                      ) : null}
                      {user.isSeedUser ? (
                        <Tooltip title={user.immutableReason ?? ''}>
                          <Chip size="small" label="default admin" variant="outlined" />
                        </Tooltip>
                      ) : null}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {user.email}
                    </Typography>
                  </TableCell>
                  <TableCell><RoleChip role={user.role} /></TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <StatusChip status={user.status} />
                      {user.mustChangePassword ? (
                        <Tooltip title="Must set a new password at next sign-in">
                          <LockResetIcon fontSize="small" color="warning" />
                        </Tooltip>
                      ) : null}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {user.lastLoginAt
                        ? new Date(user.lastLoginAt).toLocaleString()
                        : 'never'}
                    </Typography>
                  </TableCell>
                  {isAdmin ? (
                    <TableCell align="right">
                      <Tooltip title="Edit">
                        <IconButton size="small" onClick={() => openEdit(user)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={user.canBeDeleted ? 'Delete' : user.immutableReason}>
                        {/* span so the tooltip still shows on a disabled button */}
                        <span>
                          <IconButton
                            size="small"
                            disabled={!user.canBeDeleted}
                            onClick={() => setConfirmDelete(user)}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={Boolean(editing)} onClose={() => setEditing(null)} fullWidth maxWidth="sm">
        <DialogTitle>{isNew ? 'Add user' : `Edit ${editing?.email ?? ''}`}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {formError ? <Alert severity="error">{formError}</Alert> : null}
            {seedLocked ? (
              <Alert severity="info">{editing.immutableReason}</Alert>
            ) : null}

            <TextField
              label="Email"
              value={form.email}
              onChange={(event) => setForm({ ...form, email: event.target.value })}
              disabled={!isNew}
              helperText={
                isNew
                  ? 'Used to sign in. It can never be changed afterwards.'
                  : 'Email can never be changed.'
              }
              required
              fullWidth
            />
            <Stack direction="row" spacing={2}>
              <TextField
                label="First name"
                value={form.firstName}
                onChange={(event) => setForm({ ...form, firstName: event.target.value })}
                required
                fullWidth
              />
              <TextField
                label="Last name"
                value={form.lastName}
                onChange={(event) => setForm({ ...form, lastName: event.target.value })}
                required
                fullWidth
              />
            </Stack>
            <TextField
              label={isNew ? 'Password' : 'New password'}
              type="password"
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
              autoComplete="new-password"
              required={isNew}
              helperText={
                isNew
                  ? 'At least 8 characters.'
                  : 'Leave blank to keep the current password.'
              }
              fullWidth
            />
            <Stack direction="row" spacing={2}>
              <TextField
                select
                label="Role"
                value={form.role}
                onChange={(event) => setForm({ ...form, role: event.target.value })}
                disabled={seedLocked}
                fullWidth
              >
                {roleOptions.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                select
                label="Status"
                value={form.status}
                onChange={(event) => setForm({ ...form, status: event.target.value })}
                disabled={seedLocked}
                helperText="Inactive accounts cannot sign in."
                fullWidth
              >
                {Object.entries(STATUS_LABELS).map(([value, label]) => (
                  <MenuItem key={value} value={value}>{label}</MenuItem>
                ))}
              </TextField>
            </Stack>
            <FormControlLabel
              control={
                <Switch
                  checked={form.mustChangePassword}
                  onChange={(event) =>
                    setForm({ ...form, mustChangePassword: event.target.checked })
                  }
                />
              }
              label="User must change password on first login"
            />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={
              saving ||
              !form.firstName ||
              !form.lastName ||
              (isNew && (!form.email || !form.password))
            }
          >
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(confirmDelete)} onClose={() => setConfirmDelete(null)}>
        <DialogTitle>Delete this user?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {confirmDelete?.fullName} ({confirmDelete?.email}) will be removed. Their
            orders, positions and notes are not deleted.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={handleDelete}>
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
