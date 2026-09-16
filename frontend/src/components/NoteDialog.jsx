import { useEffect, useState } from 'react';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { notesApi } from '../api/reports';

/** Attach or edit a free-text note on a completed trade. */
export default function NoteDialog({ open, onClose, onSaved, note, order }) {
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setText(note?.noteText ?? '');
    setError(null);
  }, [open, note]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (note?.id) {
        await notesApi.update(note.id, { noteText: text });
      } else {
        await notesApi.create({
          noteText: text,
          orderId: order?.id,
          securityId: order?.securityId,
        });
      }
      onSaved?.();
      onClose?.();
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{note?.id ? 'Edit note' : 'Add note'}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          {order ? (
            <Typography variant="body2" color="text.secondary">
              {order.tradingSymbol} · {order.side} {order.lots} lot(s) ·{' '}
              {order.status}
            </Typography>
          ) : null}
          <TextField
            multiline
            minRows={5}
            fullWidth
            autoFocus
            label="Note"
            placeholder="What happened, what you'd do differently…"
            value={text}
            onChange={(event) => setText(event.target.value)}
            error={Boolean(error)}
            helperText={error ?? ' '}
          />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="contained" onClick={save} disabled={saving || !text.trim()}>
          {saving ? 'Saving…' : 'Save note'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
