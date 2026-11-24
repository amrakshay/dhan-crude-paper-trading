import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { optionsTraderApi } from '../api/optionsTrader';
import { useMarketFeed } from '../market/MarketFeedContext';
import { useActivePortfolio } from '../portfolios/ActivePortfolioContext';
import SyntheticBanner from '../components/SyntheticBanner';
import OptionsBasket from '../components/OptionsBasket';
import OptionPositionGroups from '../components/OptionPositionGroups';
import OptionsRiskDesk from '../components/OptionsRiskDesk';
import OptionsRestingBaskets from '../components/OptionsRestingBaskets';
import { TEMPLATES, pickTemplate } from '../options/templates';
import { formatCompact, formatPrice, formatQty } from '../utils/format';

/**
 * NSE Options Trader (OPTIONS_TRADER_PLAN.md, Phase 1).
 *
 * Any NSE index or F&O stock's chain; B and S add legs to a basket that is
 * previewed on NSE SPAN and placed leg by leg, buys first (Phase 2). The layout follows the mock the owner reviewed
 * (mocks/options-trader/index.html); the colours do NOT -- everything here is
 * `theme.palette.*` / `theme.market.*` (frontend/CLAUDE.md section 1).
 *
 * Where the numbers come from, kept apart on purpose:
 *  - LTP, bid, ask: the ONE shared socket. The server pins the ATM window to
 *    the feed while this page asks for the chain.
 *  - IV, delta, OI: Dhan's option chain, merged into the same socket rows and
 *    also returned by the chain endpoint, polled at CHAIN_POLL_MS -- that poll
 *    is also what keeps the pin alive. It is NOT tick data (section 2).
 */
const CHAIN_POLL_MS = 10000;


function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(digits);
}

function firstDefined(...values) {
  return values.find((value) => value !== null && value !== undefined);
}

/** B / S on one side of a strike: adds a leg to the basket; never places. */
function TradeButtons({ contract, onTrade }) {
  const theme = useTheme();
  if (!contract) return null;
  const base = { minWidth: 0, px: 0.75, py: 0, fontSize: 11, fontWeight: 700, lineHeight: 1.6 };
  return (
    <Stack direction="row" spacing={0.5} justifyContent="center">
      <Button
        size="small"
        variant="contained"
        disableElevation
        onClick={() => onTrade(contract, 'BUY')}
        sx={{ ...base, bgcolor: theme.market.up, '&:hover': { bgcolor: theme.market.up } }}
      >
        B
      </Button>
      <Button
        size="small"
        variant="contained"
        disableElevation
        onClick={() => onTrade(contract, 'SELL')}
        sx={{ ...base, bgcolor: theme.market.down, '&:hover': { bgcolor: theme.market.down } }}
      >
        S
      </Button>
    </Stack>
  );
}

/**
 * One strike. Memoised on the LIVE row objects from the feed context, whose
 * identity changes only when that instrument ticked (frontend/CLAUDE.md s.2),
 * so a 60-strike chain re-renders just the rows that moved.
 */
const StrikeRow = memo(function StrikeRow({ row, liveCall, livePut, isAtm, spot, onTrade }) {
  const theme = useTheme();
  const callItm = spot !== null && row.strike < spot;
  const putItm = spot !== null && row.strike > spot;
  const itm = { bgcolor: theme.market.itm };
  const cell = { whiteSpace: 'nowrap', py: 0.5 };

  const side = (contract, live, isItm) => {
    const sx = { ...cell, ...(isItm ? itm : {}) };
    const ltp = firstDefined(live?.ltp, contract?.ltp, contract?.chainLtp);
    const bid = firstDefined(live?.bid, contract?.bid, contract?.chainBid);
    const ask = firstDefined(live?.ask, contract?.ask, contract?.chainAsk);
    const oi = firstDefined(live?.oi, contract?.oi, contract?.chainOi);
    const iv = firstDefined(live?.iv, contract?.iv);
    const delta = firstDefined(live?.delta, contract?.delta);
    return { sx, ltp, bid, ask, oi, iv, delta };
  };
  const c = side(row.call, liveCall, callItm);
  const p = side(row.put, livePut, putItm);

  return (
    <TableRow
      className="numeric"
      sx={isAtm ? { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: '-2px' } : undefined}
    >
      <TableCell align="center" sx={c.sx}>
        <TradeButtons contract={row.call} onTrade={onTrade} />
      </TableCell>
      <TableCell align="right" sx={c.sx}>{formatCompact(c.oi)}</TableCell>
      <TableCell align="right" sx={c.sx}>{num(c.iv, 1)}</TableCell>
      <TableCell align="right" sx={c.sx}>{num(c.delta, 2)}</TableCell>
      <TableCell align="right" sx={{ ...c.sx, color: 'text.secondary' }}>
        {formatPrice(c.bid)} / {formatPrice(c.ask)}
      </TableCell>
      <TableCell align="right" sx={{ ...c.sx, fontWeight: 600 }}>{formatPrice(c.ltp)}</TableCell>

      <TableCell
        align="center"
        sx={{
          ...cell,
          fontWeight: 700,
          bgcolor: isAtm ? theme.market.atm : theme.palette.action.hover,
          borderLeft: `1px solid ${theme.palette.divider}`,
          borderRight: `1px solid ${theme.palette.divider}`,
        }}
      >
        {formatQty(row.strike)}
      </TableCell>

      <TableCell align="right" sx={{ ...p.sx, fontWeight: 600 }}>{formatPrice(p.ltp)}</TableCell>
      <TableCell align="right" sx={{ ...p.sx, color: 'text.secondary' }}>
        {formatPrice(p.bid)} / {formatPrice(p.ask)}
      </TableCell>
      <TableCell align="right" sx={p.sx}>{num(p.delta, 2)}</TableCell>
      <TableCell align="right" sx={p.sx}>{num(p.iv, 1)}</TableCell>
      <TableCell align="right" sx={p.sx}>{formatCompact(p.oi)}</TableCell>
      <TableCell align="center" sx={p.sx}>
        <TradeButtons contract={row.put} onTrade={onTrade} />
      </TableCell>
    </TableRow>
  );
});

function Chain({ chain, onTrade }) {
  const theme = useTheme();
  const { getRow, version } = useMarketFeed();
  const live = useMemo(() => {
    const map = {};
    chain.rows.forEach((row) => {
      if (row.call) map[row.call.securityId] = getRow(row.call.securityId);
      if (row.put) map[row.put.securityId] = getRow(row.put.securityId);
    });
    return map;
    // `version` is what tells us the feed moved; getRow is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chain, getRow, version]);
  const liveSpot = chain.spotSecurityId ? getRow(chain.spotSecurityId)?.ltp : null;
  const spot = firstDefined(liveSpot, chain.spot, null);

  // Only the strikes on the feed are shown: the window the server pinned.
  const rows = chain.rows.filter((row) => row.inWindow);

  return (
    <TableContainer sx={{ maxHeight: 'calc(100vh - 360px)' }}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            <TableCell colSpan={6} align="center" sx={{ color: theme.market.up, fontWeight: 700 }}>
              CALLS
            </TableCell>
            <TableCell align="center">Strike</TableCell>
            <TableCell colSpan={6} align="center" sx={{ color: theme.market.down, fontWeight: 700 }}>
              PUTS
            </TableCell>
          </TableRow>
          <TableRow>
            {['', 'OI', 'IV', 'Δ', 'Bid / Ask', 'LTP'].map((label, index) => (
              <TableCell key={`c${index}`} align={index ? 'right' : 'center'}>{label}</TableCell>
            ))}
            <TableCell align="center" />
            {['LTP', 'Bid / Ask', 'Δ', 'IV', 'OI', ''].map((label, index) => (
              <TableCell key={`p${index}`} align={index === 5 ? 'center' : 'right'}>{label}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <StrikeRow
              key={row.strike}
              row={row}
              liveCall={row.call ? live[row.call.securityId] : null}
              livePut={row.put ? live[row.put.securityId] : null}
              isAtm={row.strike === chain.atmStrike}
              spot={spot}
              onTrade={onTrade}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export default function OptionsTraderPage() {
  const theme = useTheme();
  const { activeId: portfolioId, active: portfolio } = useActivePortfolio();
  const [underlyings, setUnderlyings] = useState(null);
  const [symbol, setSymbol] = useState('NIFTY');
  const [expiry, setExpiry] = useState(null);
  const [chain, setChain] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [legs, setLegs] = useState([]);
  const [placedCount, setPlacedCount] = useState(0);
  const [notice, setNotice] = useState(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    optionsTraderApi
      .underlyings()
      .then((data) => {
        setUnderlyings(data.underlyings);
        if (data.underlyings.length && !data.underlyings.some((one) => one.symbol === 'NIFTY')) {
          setSymbol(data.underlyings[0].symbol);
        }
      })
      .catch((loadError) => setError(loadError.message));
  }, []);

  const loadChain = useCallback(async () => {
    if (!symbol) return;
    const seq = ++requestSeq.current;
    try {
      const data = await optionsTraderApi.chain(symbol, expiry);
      if (seq !== requestSeq.current) return;
      setChain(data);
      setError(null);
    } catch (loadError) {
      if (seq === requestSeq.current) setError(loadError.message);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [symbol, expiry]);

  useEffect(() => {
    setLoading(true);
    loadChain();
    const timer = window.setInterval(loadChain, CHAIN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadChain]);

  // Leaving the page releases the pinned strikes now, rather than after the
  // server's TTL. Best effort: the TTL is the backstop.
  // A reload or a closed tab never unmounts, so `pagehide` covers those.
  useEffect(() => {
    const release = () => { optionsTraderApi.release().catch(() => {}); };
    window.addEventListener('pagehide', release);
    return () => {
      window.removeEventListener('pagehide', release);
      release();
    };
  }, []);

  const selected = underlyings?.find((one) => one.symbol === symbol) ?? null;
  // B / S add a leg. A basket holds ONE underlying: the payoff and the SPAN
  // preview are about one book of risk, and mixing two would mislead both.
  const legFor = useCallback(
    (contract, row, optionType, side) => ({
      securityId: contract.securityId,
      tradingSymbol: contract.tradingSymbol,
      underlying: chain?.underlying?.symbol,
      side,
      lots: 1,
      lotSize: contract.lotSize,
      orderType: 'MARKET',
      limitPrice: '',
      optionType,
      strike: row?.strike,
      price: contract.ltp ?? contract.chainLtp ?? null,
    }),
    [chain],
  );

  // A template REPLACES the basket: it is a whole position, and adding it to
  // legs already there would build something nobody chose.
  const [templateKey, setTemplateKey] = useState(TEMPLATES[0].key);
  const [templateWidth, setTemplateWidth] = useState(2);
  const applyTemplate = () => {
    const template = TEMPLATES.find((one) => one.key === templateKey);
    const { picks, missing } = pickTemplate(template, chain, templateWidth);
    if (missing) {
      setNotice(missing);
      return;
    }
    setLegs(picks.map(({ contract, row, optionType, side }) => legFor(contract, row, optionType, side)));
    setNotice(`${template.label} built from ATM ${formatPrice(chain.atmStrike)}, ${templateWidth} strike(s) wide. Nothing is placed until you confirm.`);
  };

  const onTrade = useCallback(
    (contract, side) => {
      const row = chain?.rows.find((one) => one.call?.securityId === contract.securityId || one.put?.securityId === contract.securityId);
      const optionType = row?.call?.securityId === contract.securityId ? 'CE' : 'PE';
      const leg = legFor(contract, row, optionType, side);
      setLegs((current) => {
        if (current.length && current[0].underlying !== leg.underlying) {
          setNotice(`Basket cleared: a basket holds one underlying (${leg.underlying} now).`);
          return [leg];
        }
        setNotice(null);
        return [...current, leg];
      });
    },
    [chain, legFor],
  );

  return (
    <Stack spacing={2}>
      <SyntheticBanner />

      <Card variant="outlined">
        <CardContent>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems={{ md: 'center' }}>
            <Autocomplete
              sx={{ width: 280 }}
              size="small"
              options={underlyings ?? []}
              loading={underlyings === null}
              value={selected}
              getOptionLabel={(option) => option.symbol}
              groupBy={(option) => (option.kind === 'INDEX' ? 'Indices' : 'Stocks')}
              isOptionEqualToValue={(option, value) => option.symbol === value.symbol}
              onChange={(_event, value) => {
                if (!value) return;
                setSymbol(value.symbol);
                setExpiry(null);
                setChain(null);
              }}
              // The value arrives after the options load, which leaves MUI's
              // floating label sitting over it unless it is told to shrink.
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Underlying"
                  InputLabelProps={{ ...params.InputLabelProps, shrink: true }}
                />
              )}
            />
            <Box>
              <Typography variant="h4" className="numeric">
                {symbol} {chain?.spot ? formatPrice(chain.spot) : ''}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {selected
                  ? `${selected.kind === 'INDEX' ? 'Index · cash-settled' : 'Stock · physically settled'} · lot ${formatQty(selected.lotSize)}`
                  : ''}
                {chain?.spotSource === 'option-chain' ? ' · spot from the option chain (~3 s)' : ''}
              </Typography>
            </Box>
            <Box sx={{ flexGrow: 1 }} />
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {(chain?.expiries ?? []).map((one) => {
                const on = one === chain?.expiry;
                return (
                  <Chip
                    key={one}
                    size="small"
                    clickable
                    label={one}
                    onClick={() => setExpiry(one)}
                    variant="outlined"
                    sx={on ? { bgcolor: theme.palette.primary.main, color: theme.palette.primary.contrastText, fontWeight: 600 } : undefined}
                  />
                );
              })}
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {error ? <Alert severity="error">{error}</Alert> : null}
      {chain?.snapshotNote ? <Alert severity="warning">{chain.snapshotNote}</Alert> : null}
      {chain?.marginNote ? (
        <Alert severity="info">
          {chain.marginNote} Portfolio: <strong>{portfolio?.name ?? 'none selected'}</strong>.
        </Alert>
      ) : null}

      {notice ? <Alert severity="info" onClose={() => setNotice(null)}>{notice}</Alert> : null}

      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1fr) 440px' },
          alignItems: 'start',
        }}
      >
        <Card variant="outlined" sx={{ minWidth: 0 }}>
          {loading && !chain ? (
            <Box sx={{ p: 3, textAlign: 'center' }}>
              <CircularProgress size={24} />
            </Box>
          ) : chain ? (
            <Chain chain={chain} onTrade={onTrade} />
          ) : null}
          {chain ? (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', p: 1 }}>
              ATM ± {chain.strikeWindow} strikes are on the live feed. IV and Δ come from Dhan's
              option chain; prices stream from the feed.
            </Typography>
          ) : null}
        </Card>
        <Box sx={{ position: { lg: 'sticky' }, top: { lg: 80 } }}>
          <Card variant="outlined" sx={{ mb: 2 }}>
            <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <TextField
                  select
                  size="small"
                  label="Template"
                  value={templateKey}
                  onChange={(event) => setTemplateKey(event.target.value)}
                  sx={{ minWidth: 200, flexGrow: 1 }}
                >
                  {TEMPLATES.map((one) => (
                    <MenuItem key={one.key} value={one.key}>{one.label}</MenuItem>
                  ))}
                </TextField>
                <TextField
                  size="small"
                  type="number"
                  label="Width (strikes)"
                  value={templateWidth}
                  onChange={(event) => setTemplateWidth(Math.max(1, Math.min(10, Number(event.target.value) || 1)))}
                  inputProps={{ min: 1, max: 10 }}
                  sx={{ width: 120 }}
                />
                <Button size="small" variant="outlined" onClick={applyTemplate} disabled={!chain?.atmStrike}>
                  Build
                </Button>
              </Stack>
            </CardContent>
          </Card>
          <OptionsBasket
            legs={legs}
            setLegs={setLegs}
            spot={chain?.spot ?? null}
            onPlaced={() => setPlacedCount((count) => count + 1)}
          />
        </Box>
      </Box>

      {portfolioId ? <OptionsRestingBaskets refreshKey={placedCount} /> : null}
      {portfolioId ? <OptionPositionGroups refreshKey={placedCount} /> : null}
      {portfolioId ? <OptionsRiskDesk refreshKey={placedCount} /> : null}
      {portfolioId ? null : (
        <Alert severity="info">Select a portfolio in the header to trade.</Alert>
      )}
    </Stack>
  );
}
