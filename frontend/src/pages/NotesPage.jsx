import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import { notesApi } from '../api/reports';
import NoteDialog from '../components/NoteDialog';

function timestamp(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('en-IN', { hour12: false });
}

export default function NotesPage() {
  const [notes, setNotes] = useState([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await notesApi.list({ q: query || undefined, size: 200 });
      setNotes(result.notes);
      setTotal(result.total);
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [query]);

  // Debounced so typing a search does not fire a request per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(load, 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  const remove = async (id) => {
    try {
      await notesApi.remove(id);
      await load();
    } catch (removeError) {
      setError(removeError.message);
    }
  };

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h2">Trade Notes</Typography>
        <Typography variant="body2" color="text.secondary">
          Free-text notes attached to completed trades, searchable and editable
        </Typography>
      </Box>

      {error ? <Alert severity="error">{error}</Alert> : null}

      <TextField
        size="small"
        placeholder="Search note text or contract (e.g. “6800 CALL”, “slippage”)"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        sx={{ maxWidth: 520 }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SearchIcon fontSize="small" />
            </InputAdornment>
          ),
        }}
      />

      {loading && notes.length === 0 ? (
        <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 160 }}>
          <CircularProgress />
        </Box>
      ) : notes.length === 0 ? (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary">
              {query
                ? `No notes match “${query}”.`
                : 'No notes yet. Add one from an order in Order History.'}
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Stack spacing={2}>
          <Typography variant="caption" color="text.secondary">
            {total} note{total === 1 ? '' : 's'}
          </Typography>
          {notes.map((note) => (
            <Card key={note.id}>
              <CardContent>
                <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={2}>
                  <Stack spacing={1} sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                      {note.tradingSymbol ? (
                        <Chip size="small" variant="outlined" label={note.tradingSymbol} />
                      ) : null}
                      {note.orderId ? (
                        <Chip size="small" variant="outlined" label={`order #${note.orderId}`} />
                      ) : null}
                      <Typography variant="caption" color="text.secondary">
                        {timestamp(note.notedAt)}
                        {note.editedAt ? ` · edited ${timestamp(note.editedAt)}` : ''}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                      {note.noteText}
                    </Typography>
                  </Stack>
                  <Stack direction="row" spacing={0.5}>
                    <Tooltip title="Edit">
                      <IconButton size="small" onClick={() => setEditing(note)}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Delete">
                      <IconButton size="small" onClick={() => remove(note.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                </Stack>
              </CardContent>
            </Card>
          ))}
        </Stack>
      )}

      <NoteDialog
        open={Boolean(editing)}
        note={editing}
        onClose={() => setEditing(null)}
        onSaved={load}
      />
    </Stack>
  );
}
