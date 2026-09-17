import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Grid,
  MenuItem,
  Paper,
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
  useTheme,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArchiveIcon from '@mui/icons-material/Archive';
import UnarchiveIcon from '@mui/icons-material/Unarchive';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { portfoliosApi } from '../api/portfolios';
import { strategiesApi } from '../api/strategies';
import { useAuth } from '../auth/AuthContext';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import { formatPrice, formatTime } from '../utils/format';

/**
 * One card per portfolio.
 *
 * Four figures, never collapsed into one: cash, blocked margin, available and
 * equity. Blocked margin always says "estimate", and an equity figure that
 * contains an unmarked position says so beside the number rather than quietly
 * valuing that position at zero (frontend/CLAUDE.md section 3).
 */

const ENTRY_LABELS = {
  DEPOSIT: 'Deposit',
  WITHDRAWAL: 'Withdrawal',
  TRADE_DEBIT: 'Trade',
  TRADE_CREDIT: 'Trade',
  CHARGES: 'Charges',
};

function Figure({ label, value, hint, emphasis = false, unknown = false, note }) {
  const theme = useTheme();
  return (
    <Box>
      <Stack direction="row" spacing={0.5} alignItems="center">
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
        {hint ? (
          <Tooltip title={hint}>
            <InfoOutlinedIcon sx={{ fontSize: 13, color: 'text.disabled' }} />
          </Tooltip>
        ) : null}
      </Stack>
      <Typography
        variant={emphasis ? 'h5' : 'body1'}
        className="numeric"
        sx={{
          fontWeight: emphasis ? 600 : 500,
          color: unknown ? theme.palette.text.disabled : 'text.primary',
        }}
      >
        {unknown ? 'no mark' : `₹${formatPrice(Number(value ?? 0))}`}
      </Typography>
      {note ? (
        <Typography variant="caption" color="warning.main">
          {note}
        </Typography>
      ) : null}
    </Box>
  );
}

function Ledger({ portfolioId, refreshToken }) {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    portfoliosApi
      .ledger(portfolioId, { size: 25 })
      .then((response) => {
        if (!cancelled) setState(response);
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError.message);
      });
    return () => {
      cancelled = true;
    };
  }, [portfolioId, refreshToken]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!state) return <CircularProgress size={18} />;
  if (state.entries.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No movements yet.
      </Typography>
    );
  }

  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell>When</TableCell>
          <TableCell>Entry</TableCell>
          <TableCell align="right">Amount</TableCell>
          <TableCell>Note</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {state.entries.map((entry) => {
          const amount = Number(entry.amount);
          return (
            <TableRow key={entry.id}>
              <TableCell sx={{ whiteSpace: 'nowrap' }}>
                {formatTime(entry.entryAt)}
              </TableCell>
              <TableCell>{ENTRY_LABELS[entry.entryType] ?? entry.entryType}</TableCell>
              <TableCell align="right" className="numeric">
                {amount >= 0 ? '+' : '−'}₹{formatPrice(Math.abs(amount))}
              </TableCell>
              <TableCell sx={{ color: 'text.secondary' }}>{entry.note ?? '—'}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function MoneyDialog({ open, mode, portfolio, onClose, onDone }) {
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const payload = { amount, note: note || undefined };
      if (mode === 'deposit') await portfoliosApi.deposit(portfolio.id, payload);
      else await portfoliosApi.withdraw(portfolio.id, payload);
      setAmount('');
      setNote('');
      setError(null);
      onDone();
      onClose();
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>
        {mode === 'deposit' ? 'Deposit into' : 'Withdraw from'} {portfolio?.name}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error ? <Alert severity="error">{error}</Alert> : null}
          {mode === 'withdraw' ? (
            <Typography variant="caption" color="text.secondary">
              At most ₹{formatPrice(Number(portfolio?.balance?.available ?? 0))} is
              available. Money blocked against open short positions cannot be
              withdrawn, even though that figure is an estimate.
            </Typography>
          ) : null}
          <TextField
            label="Amount"
            size="small"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            inputProps={{ inputMode: 'decimal' }}
            autoFocus
          />
          <TextField
            label="Note (optional)"
            size="small"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={busy || !amount || Number(amount) <= 0}
        >
          {mode === 'deposit' ? 'Deposit' : 'Withdraw'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function CreateDialog({ open, onClose, onDone, strategies }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [opening, setOpening] = useState('1000000');
  const [selected, setSelected] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await portfoliosApi.create({
        name,
        description: description || undefined,
        strategies: selected,
        openingBalance: opening || '0',
      });
      setError(null);
      onDone();
      onClose();
      setName('');
      setDescription('');
      setSelected([]);
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>New portfolio</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error ? <Alert severity="error">{error}</Alert> : null}
          <TextField
            label="Name"
            size="small"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
          <TextField
            label="Description (optional)"
            size="small"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <TextField
            label="Opening balance"
            size="small"
            value={opening}
            onChange={(event) => setOpening(event.target.value)}
            helperText="Recorded as the first deposit in the ledger."
            inputProps={{ inputMode: 'decimal' }}
          />
          <TextField
            select
            SelectProps={{ multiple: true }}
            label="Strategies"
            size="small"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            helperText="Which strategy modules this portfolio may trade."
          >
            {strategies.map((strategy) => (
              <MenuItem key={strategy.key} value={strategy.key}>
                {strategy.label}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={busy || !name.trim()}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function PortfoliosPage() {
  const { isAdmin } = useAuth();
  const { refresh: refreshHeader, activeId, select } = useActivePortfolio();
  const [portfolios, setPortfolios] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [money, setMoney] = useState(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await portfoliosApi.list(includeArchived);
      setPortfolios(response.portfolios ?? []);
      setError(null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [includeArchived]);

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  useEffect(() => {
    strategiesApi
      .list()
      .then((response) => setStrategies(response.strategies ?? []))
      .catch(() => setStrategies([]));
  }, []);

  const reload = useCallback(() => {
    setRefreshToken((token) => token + 1);
    refreshHeader();
  }, [refreshHeader]);

  const total = useMemo(
    () =>
      portfolios.reduce((sum, portfolio) => sum + Number(portfolio.balance?.cash ?? 0), 0),
    [portfolios],
  );

  return (
    <Stack spacing={3}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        gap={2}
      >
        <Box>
          <Typography variant="h2">Portfolios</Typography>
          <Typography variant="body2" color="text.secondary">
            Paper money, one book per portfolio. ₹{formatPrice(total)} across{' '}
            {portfolios.length} {portfolios.length === 1 ? 'portfolio' : 'portfolios'}.
          </Typography>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center">
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={includeArchived}
                onChange={(event) => setIncludeArchived(event.target.checked)}
              />
            }
            label={<Typography variant="body2">Show archived</Typography>}
          />
          {isAdmin ? (
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => setCreating(true)}
            >
              New portfolio
            </Button>
          ) : null}
        </Stack>
      </Stack>

      {error ? <Alert severity="error">{error}</Alert> : null}
      {loading ? <CircularProgress size={22} /> : null}

      {portfolios.map((portfolio) => {
        const balance = portfolio.balance ?? {};
        const archived = portfolio.status === 'ARCHIVED';
        const unmarked = Number(balance.unmarkedPositions ?? 0);
        return (
          <Paper key={portfolio.id} variant="outlined" sx={{ p: 2.5 }}>
            <Stack
              direction="row"
              alignItems="flex-start"
              justifyContent="space-between"
              flexWrap="wrap"
              gap={2}
            >
              <Box>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="h5" sx={{ fontWeight: 600 }}>
                    {portfolio.name}
                  </Typography>
                  {archived ? (
                    <Chip size="small" label="Archived" variant="outlined" />
                  ) : null}
                  {portfolio.id === activeId ? (
                    <Chip
                      size="small"
                      label="Active"
                      // See StrategiesPage: the theme forces a chip background,
                      // so `color="primary"` alone gives white on grey.
                      sx={{ bgcolor: 'primary.main', color: 'primary.contrastText' }}
                    />
                  ) : null}
                </Stack>
                <Typography variant="body2" color="text.secondary">
                  {portfolio.description || 'No description'}
                </Typography>
                <Stack direction="row" spacing={0.5} sx={{ mt: 1 }} flexWrap="wrap">
                  {portfolio.strategies.length === 0 ? (
                    <Chip size="small" variant="outlined" label="No strategies attached" />
                  ) : (
                    portfolio.strategies.map((key) => (
                      <Chip
                        key={key}
                        size="small"
                        variant="outlined"
                        label={
                          strategies.find((strategy) => strategy.key === key)?.label ?? key
                        }
                      />
                    ))
                  )}
                </Stack>
              </Box>

              <Stack direction="row" spacing={1}>
                {!archived && portfolio.id !== activeId ? (
                  <Button size="small" onClick={() => select(portfolio.id)}>
                    Make active
                  </Button>
                ) : null}
                {isAdmin && !archived ? (
                  <>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => setMoney({ mode: 'deposit', portfolio })}
                    >
                      Deposit
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => setMoney({ mode: 'withdraw', portfolio })}
                    >
                      Withdraw
                    </Button>
                    <Tooltip title="Hidden from the pickers. Every trade and ledger entry stays readable — a portfolio with history is never deleted.">
                      <Button
                        size="small"
                        startIcon={<ArchiveIcon />}
                        onClick={async () => {
                          await portfoliosApi.archive(portfolio.id);
                          reload();
                        }}
                      >
                        Archive
                      </Button>
                    </Tooltip>
                  </>
                ) : null}
                {isAdmin && archived ? (
                  <Button
                    size="small"
                    startIcon={<UnarchiveIcon />}
                    onClick={async () => {
                      await portfoliosApi.restore(portfolio.id);
                      reload();
                    }}
                  >
                    Restore
                  </Button>
                ) : null}
              </Stack>
            </Stack>

            <Divider sx={{ my: 2 }} />

            <Grid container spacing={3}>
              <Grid item xs={6} sm={3}>
                <Figure
                  label="Cash"
                  value={balance.cash}
                  hint="The sum of every ledger entry. Never a stored total."
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <Figure
                  label="Blocked margin (estimate)"
                  value={balance.blockedMargin}
                  hint="An APPROXIMATION of what open short positions tie up. Real margin is SPAN + exposure, computed by the exchange from files this tool does not consume. It is not what a broker would block."
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <Figure
                  label="Available"
                  value={balance.available}
                  hint="Cash less blocked margin. This is what can be traded or withdrawn."
                  emphasis
                />
              </Grid>
              <Grid item xs={6} sm={3}>
                <Figure
                  label="Equity"
                  value={balance.equity}
                  unknown={balance.equity === null || balance.equity === undefined}
                  hint="Cash plus the mark-to-market of open positions."
                  note={
                    unmarked > 0
                      ? `${unmarked} open position${unmarked === 1 ? '' : 's'} ${
                          unmarked === 1 ? 'has' : 'have'
                        } no live mark${
                          balance.unmarkedStrategies?.length
                            ? ` (${balance.unmarkedStrategies.join(', ')})`
                            : ''
                        }, so equity cannot be computed.`
                      : null
                  }
                />
              </Grid>
            </Grid>

            <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
              {balance.openPositions ?? 0} open position
              {(balance.openPositions ?? 0) === 1 ? '' : 's'}
            </Typography>

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Cash ledger
              <Typography component="span" variant="caption" color="text.secondary">
                {' '}
                — append-only; a correction is a new entry
              </Typography>
            </Typography>
            <Ledger portfolioId={portfolio.id} refreshToken={refreshToken} />
          </Paper>
        );
      })}

      {money ? (
        <MoneyDialog
          open
          mode={money.mode}
          portfolio={money.portfolio}
          onClose={() => setMoney(null)}
          onDone={reload}
        />
      ) : null}
      <CreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onDone={reload}
        strategies={strategies}
      />
    </Stack>
  );
}
