import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  CircularProgress,
  Collapse,
  FormControl,
  FormControlLabel,
  Grid,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import NoteAddIcon from '@mui/icons-material/NoteAdd';
import DownloadIcon from '@mui/icons-material/Download';
import CancelIcon from '@mui/icons-material/Cancel';
import { useTheme } from '@mui/material/styles';
import { ordersApi } from '../api/trading';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { notesApi, reportsApi } from '../api/reports';
import ChargesBreakdown from '../components/ChargesBreakdown';
import NoteDialog from '../components/NoteDialog';
import { formatPrice, formatQty } from '../utils/format';

const STATUSES = [
  'PENDING', 'OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'REJECTED',
];

const STATUS_COLOR = {
  FILLED: 'success',
  PARTIALLY_FILLED: 'warning',
  OPEN: 'info',
  PENDING: 'default',
  CANCELLED: 'default',
  REJECTED: 'error',
};

/** Millisecond-precision timestamp, in IST. */
function timestamp(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  const time = date.toLocaleTimeString('en-IN', { hour12: false });
  const ms = String(date.getMilliseconds()).padStart(3, '0');
  return `${date.toLocaleDateString('en-IN')} ${time}.${ms}`;
}

function OrderDetail({ order, notes, onAddNote }) {
  const theme = useTheme();
  return (
    <Box sx={{ p: 3, bgcolor: 'action.hover' }}>
      <Grid container spacing={4}>
        <Grid item xs={12} md={5}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            State transitions
          </Typography>
          <Stack spacing={0.75}>
            {order.events.map((event, index) => (
              <Stack key={index} direction="row" spacing={1.5} alignItems="flex-start">
                <Typography variant="caption" className="numeric" color="text.secondary" sx={{ minWidth: 150 }}>
                  {timestamp(event.eventAt)}
                </Typography>
                <Chip size="small" label={event.eventType} variant="outlined" sx={{ minWidth: 92 }} />
                <Typography variant="caption" color="text.secondary">
                  {event.message}
                </Typography>
              </Stack>
            ))}
          </Stack>

          {order.fills.length > 0 ? (
            <>
              <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>
                Fills
              </Typography>
              <Table size="small" className="numeric">
                <TableHead>
                  <TableRow>
                    <TableCell align="right">Qty</TableCell>
                    <TableCell align="right">Price</TableCell>
                    <TableCell align="right">Book level</TableCell>
                    <TableCell align="right">Touch</TableCell>
                    <TableCell align="right">Slippage</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {order.fills.map((fill, index) => (
                    <TableRow key={index}>
                      <TableCell align="right">{formatQty(fill.quantity)}</TableCell>
                      <TableCell align="right">{formatPrice(Number(fill.price))}</TableCell>
                      <TableCell align="right">{fill.bookLevel ?? '—'}</TableCell>
                      <TableCell align="right">
                        {fill.referencePrice ? formatPrice(Number(fill.referencePrice)) : '—'}
                      </TableCell>
                      <TableCell align="right">{fill.slippageTicks} ticks</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          ) : null}
        </Grid>

        <Grid item xs={12} md={3}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Charges
          </Typography>
          {order.charges ? (
            <ChargesBreakdown
              charges={{
                // The line items come from the rate card this order was
                // charged under, so they are passed through rather than
                // picked out field by field.
                components: order.charges.components,
                total: order.charges.totalCharges,
                ratesVersion: order.charges.ratesVersion,
              }}
              dense
            />
          ) : (
            <Typography variant="body2" color="text.secondary">
              No charges — this order never executed.
            </Typography>
          )}
        </Grid>

        <Grid item xs={12} md={4}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
            <Typography variant="subtitle2">Notes</Typography>
            <Button size="small" startIcon={<NoteAddIcon />} onClick={() => onAddNote(order)}>
              Add
            </Button>
          </Stack>
          {notes.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No notes on this order yet.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {notes.map((note) => (
                <Card key={note.id} sx={{ p: 1.5 }}>
                  <Typography variant="body2">{note.noteText}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {timestamp(note.notedAt)}
                    {note.editedAt ? ' · edited' : ''}
                  </Typography>
                </Card>
              ))}
            </Stack>
          )}
        </Grid>
      </Grid>
    </Box>
  );
}

export default function OrderHistoryPage() {
  const theme = useTheme();
  const [data, setData] = useState(null);
  const [notesByOrder, setNotesByOrder] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [noteTarget, setNoteTarget] = useState(null);
  const [allPortfolios, setAllPortfolios] = useState(false);
  const { activeId: portfolioId, active: portfolio } = useActivePortfolio();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await ordersApi.list({
        status: status ? [status] : undefined,
        search: search || undefined,
        // The header's scope, unless the user asked for everything. History is
        // never hidden by a filter you cannot turn off.
        portfolioId: allPortfolios ? undefined : portfolioId ?? undefined,
        page,
        size,
      });
      setData(result);

      const notes = await notesApi.list({ size: 500 });
      const grouped = {};
      for (const note of notes.notes) {
        if (note.orderId === null || note.orderId === undefined) continue;
        (grouped[note.orderId] ??= []).push(note);
      }
      setNotesByOrder(grouped);
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [status, search, page, size, portfolioId, allPortfolios]);

  useEffect(() => {
    load();
  }, [load]);

  const cancel = async (orderId) => {
    try {
      await ordersApi.cancel(orderId);
      await load();
    } catch (cancelError) {
      setError(cancelError.message);
    }
  };

  const orders = data?.orders ?? [];

  return (
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h2">Order History</Typography>
          <Typography variant="body2" color="text.secondary">
            Every order with its state transitions, fills and charges
            {allPortfolios
              ? ' — all portfolios'
              : portfolio
                ? ` — ${portfolio.name}`
                : ''}
          </Typography>
        </Box>
        <Button
          startIcon={<DownloadIcon />}
          variant="outlined"
          component="a"
          href={reportsApi.ordersCsvUrl({})}
        >
          Export CSV
        </Button>
      </Stack>

      {error ? <Alert severity="error">{error}</Alert> : null}

      <Stack direction="row" spacing={2} flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="status-label">Status</InputLabel>
          <Select
            labelId="status-label"
            label="Status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(0);
            }}
          >
            <MenuItem value="">All</MenuItem>
            {STATUSES.map((value) => (
              <MenuItem key={value} value={value}>
                {value}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={allPortfolios}
              onChange={(event) => {
                setAllPortfolios(event.target.checked);
                setPage(0);
              }}
            />
          }
          label={<Typography variant="body2">All portfolios</Typography>}
        />
        <TextField
          size="small"
          label="Search contract"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(0);
          }}
          sx={{ minWidth: 260 }}
        />
      </Stack>

      {loading && !data ? (
        <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 200 }}>
          <CircularProgress />
        </Box>
      ) : orders.length === 0 ? (
        <Card sx={{ p: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No orders yet.
          </Typography>
        </Card>
      ) : (
        <Card>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell width={40} />
                  <TableCell>Placed (IST)</TableCell>
                  <TableCell>Contract</TableCell>
                  <TableCell>Side</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell align="right">Lots</TableCell>
                  <TableCell align="right">Filled</TableCell>
                  <TableCell align="right">Avg price</TableCell>
                  <TableCell align="right">Charges</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {orders.map((order) => {
                  const isOpen = expanded === order.id;
                  const notes = notesByOrder[order.id] ?? [];
                  const cancellable = ['OPEN', 'PARTIALLY_FILLED', 'PENDING'].includes(order.status);
                  return (
                    <Fragment key={order.id}>
                      <TableRow className="numeric">
                        <TableCell>
                          <IconButton size="small" onClick={() => setExpanded(isOpen ? null : order.id)}>
                            {isOpen ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                          </IconButton>
                        </TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{timestamp(order.placedAt)}</TableCell>
                        <TableCell>{order.tradingSymbol}</TableCell>
                        <TableCell>
                          <Typography
                            variant="body2"
                            sx={{
                              color: order.side === 'BUY' ? theme.market.up : theme.market.down,
                              fontWeight: 600,
                            }}
                          >
                            {order.side}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          {order.orderType}
                          {order.limitPrice ? ` @ ${formatPrice(Number(order.limitPrice))}` : ''}
                        </TableCell>
                        <TableCell align="right">{order.lots}</TableCell>
                        <TableCell align="right">
                          {formatQty(order.filledQuantity)}/{formatQty(order.quantity)}
                        </TableCell>
                        <TableCell align="right">
                          {order.averageFillPrice ? formatPrice(Number(order.averageFillPrice)) : '—'}
                        </TableCell>
                        <TableCell align="right">
                          {order.charges ? formatPrice(Number(order.charges.totalCharges)) : '—'}
                        </TableCell>
                        <TableCell>
                          <Tooltip title={order.rejectionReason ?? ''}>
                            <Chip
                              size="small"
                              label={order.status.replace('_', ' ')}
                              color={STATUS_COLOR[order.status] ?? 'default'}
                              variant="outlined"
                            />
                          </Tooltip>
                        </TableCell>
                        <TableCell align="right">
                          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                            {notes.length > 0 ? (
                              <Chip size="small" label={`${notes.length} note`} variant="outlined" />
                            ) : null}
                            <Tooltip title="Add note">
                              <IconButton size="small" onClick={() => setNoteTarget(order)}>
                                <NoteAddIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                            {cancellable ? (
                              <Tooltip title="Cancel order">
                                <IconButton size="small" onClick={() => cancel(order.id)}>
                                  <CancelIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            ) : null}
                          </Stack>
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell colSpan={11} sx={{ p: 0, border: 0 }}>
                          <Collapse in={isOpen} unmountOnExit>
                            <OrderDetail order={order} notes={notes} onAddNote={setNoteTarget} />
                          </Collapse>
                        </TableCell>
                      </TableRow>
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
          <TablePagination
            component="div"
            count={data?.total ?? 0}
            page={page}
            onPageChange={(_event, value) => setPage(value)}
            rowsPerPage={size}
            onRowsPerPageChange={(event) => {
              setSize(Number(event.target.value));
              setPage(0);
            }}
            rowsPerPageOptions={[10, 25, 50, 100]}
          />
        </Card>
      )}

      <NoteDialog
        open={Boolean(noteTarget)}
        order={noteTarget}
        onClose={() => setNoteTarget(null)}
        onSaved={load}
      />
    </Stack>
  );
}
