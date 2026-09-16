/**
 * Draggable stop-loss and take-profit lines on the price chart.
 *
 * lightweight-charts has no draggable price line -- `draggable` does not exist
 * anywhere in its API. So the LINE is a native price line (it gets the axis
 * label and clips correctly), and the HANDLE is an HTML chip positioned over
 * the chart with `series.priceToCoordinate()`, dragged back into a price with
 * `series.coordinateToPrice()`.
 *
 * The handles are repositioned on an animation frame by mutating `style.top`
 * directly. They must follow every pan, zoom and autoscale, and doing that
 * through React state would re-render the chart chrome at 60 fps for no reason
 * (frontend/CLAUDE.md section 2).
 *
 * Levels are prices of the UNDERLYING FUTURE. Nothing is armed until the drag
 * ends: the server is told the new level on pointer-up, not on every pixel.
 */
import { useEffect, useRef } from 'react';
import { LineStyle } from 'lightweight-charts';
import { useTheme } from '@mui/material/styles';
import { useMarketRow } from '../market/MarketFeedContext';

const KIND_STOP = 'stop';
const KIND_TARGET = 'target';

function formatLevel(value) {
  return Number(value).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function BracketLines({
  chartRef,
  seriesRef,
  containerRef,
  trade,
  underlyingPrice,
  onCommit,
  onRemove,
}) {
  const theme = useTheme();
  // Subscribed here rather than in the parent: this component renders two small
  // chips, so its re-render at the feed's cadence is cheap, while the chart's
  // chrome stays out of it. Delta comes from the 3 s option-chain poller.
  const optionDelta = useMarketRow(trade?.optionSecurityId)?.delta ?? null;
  const stopNodeRef = useRef(null);
  const targetNodeRef = useRef(null);
  const linesRef = useRef({ [KIND_STOP]: null, [KIND_TARGET]: null });
  const levelsRef = useRef({ [KIND_STOP]: null, [KIND_TARGET]: null });
  const draggingRef = useRef(null);
  const labelsRef = useRef({ [KIND_STOP]: null, [KIND_TARGET]: null });

  const nodeFor = (kind) => (kind === KIND_STOP ? stopNodeRef.current : targetNodeRef.current);
  const colourFor = (kind) => (kind === KIND_STOP ? theme.market.down : theme.market.up);

  // --- keep the native price lines in step with the server's levels -------
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const wanted = {
      [KIND_STOP]: trade?.stopLossLevel != null ? Number(trade.stopLossLevel) : null,
      [KIND_TARGET]: trade?.takeProfitLevel != null ? Number(trade.takeProfitLevel) : null,
    };

    [KIND_STOP, KIND_TARGET].forEach((kind) => {
      const level = wanted[kind];
      const existing = linesRef.current[kind];

      if (level == null) {
        if (existing) {
          series.removePriceLine(existing);
          linesRef.current[kind] = null;
        }
        levelsRef.current[kind] = null;
        return;
      }
      // Mid-drag the user's hand wins over a poll that landed underneath it.
      if (draggingRef.current === kind) return;

      levelsRef.current[kind] = level;
      if (existing) {
        existing.applyOptions({ price: level, color: colourFor(kind) });
      } else {
        linesRef.current[kind] = series.createPriceLine({
          price: level,
          color: colourFor(kind),
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: kind === KIND_STOP ? 'SL' : 'TP',
        });
      }
    });
  }, [trade, seriesRef, theme]);

  // --- drop every line when the trade goes away ---------------------------
  useEffect(() => {
    if (trade) return undefined;
    return () => {
      const series = seriesRef.current;
      [KIND_STOP, KIND_TARGET].forEach((kind) => {
        if (linesRef.current[kind] && series) series.removePriceLine(linesRef.current[kind]);
        linesRef.current[kind] = null;
        levelsRef.current[kind] = null;
      });
    };
  }, [trade, seriesRef]);

  // --- follow the chart: pan, zoom and autoscale all move the pixels ------
  useEffect(() => {
    let frame = 0;
    const tick = () => {
      const series = seriesRef.current;
      // A level outside the visible range still has a coordinate, just one
      // outside the price pane -- left alone the handle drifts down over the
      // volume pane. Pin it to the edge instead, the way a chart usually does,
      // so it stays grabbable and the label keeps telling the truth.
      const paneHeight = chartRef?.current?.panes?.()[0]?.getHeight?.() ?? null;

      [KIND_STOP, KIND_TARGET].forEach((kind) => {
        const node = nodeFor(kind);
        if (!node) return;
        const level = levelsRef.current[kind];
        if (level == null || !series) {
          node.style.display = 'none';
          return;
        }
        const y = series.priceToCoordinate(level);
        if (y == null) {
          node.style.display = 'none';
          return;
        }
        const limit = paneHeight ? paneHeight - 10 : null;
        const clamped = limit == null ? y : Math.min(Math.max(y, 10), limit);
        node.style.display = 'flex';
        node.style.top = `${clamped}px`;
        node.style.opacity = clamped === y ? '1' : '0.75';
      });
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [seriesRef, chartRef]);

  // --- dragging -----------------------------------------------------------
  const describe = (kind, level) => {
    const distance =
      underlyingPrice != null ? Number(level) - Number(underlyingPrice) : null;
    const points = distance == null ? '' : ` (${distance >= 0 ? '+' : '−'}${Math.abs(distance).toFixed(0)} pts)`;
    // First-order estimate only: delta moves as the future moves, so this is
    // what the option is worth NOW per point, not what it will be worth then.
    let money = '';
    if (distance != null && optionDelta != null && trade?.quantity) {
      const estimate = Number(optionDelta) * distance * Number(trade.quantity);
      money = ` ≈ ${estimate >= 0 ? '+' : '−'}₹${Math.abs(estimate).toFixed(0)} est.`;
    }
    return `${kind === KIND_STOP ? 'SL' : 'TP'} ${formatLevel(level)}${points}${money}`;
  };

  const paint = (kind, level) => {
    levelsRef.current[kind] = level;
    linesRef.current[kind]?.applyOptions({ price: level });
    if (labelsRef.current[kind]) labelsRef.current[kind].textContent = describe(kind, level);
  };

  const handlePointerDown = (kind) => (event) => {
    if (levelsRef.current[kind] == null) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    draggingRef.current = kind;
  };

  const handlePointerMove = (kind) => (event) => {
    if (draggingRef.current !== kind) return;
    const series = seriesRef.current;
    const container = containerRef.current;
    if (!series || !container) return;
    const y = event.clientY - container.getBoundingClientRect().top;
    const price = series.coordinateToPrice(y);
    if (price == null) return;
    paint(kind, Math.round(Number(price) * 100) / 100);
  };

  const handlePointerUp = (kind) => (event) => {
    if (draggingRef.current !== kind) return;
    draggingRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    const level = levelsRef.current[kind];
    if (level != null) onCommit(kind, level);
  };

  const handleStyle = (kind) => ({
    position: 'absolute',
    left: 8,
    display: 'none',
    alignItems: 'center',
    gap: '6px',
    transform: 'translateY(-50%)',
    padding: '2px 8px',
    borderRadius: 4,
    fontSize: 12,
    fontVariantNumeric: 'tabular-nums',
    color: '#fff',
    background: colourFor(kind),
    cursor: 'ns-resize',
    userSelect: 'none',
    touchAction: 'none',
    zIndex: 2,
    whiteSpace: 'nowrap',
    boxShadow: theme.palette.mode === 'dark' ? '0 0 0 1px rgba(0,0,0,0.4)' : 'none',
  });

  const renderHandle = (kind, nodeRef) => (
    <div
      ref={nodeRef}
      style={handleStyle(kind)}
      onPointerDown={handlePointerDown(kind)}
      onPointerMove={handlePointerMove(kind)}
      onPointerUp={handlePointerUp(kind)}
      onPointerCancel={handlePointerUp(kind)}
    >
      <span ref={(node) => { labelsRef.current[kind] = node; }}>
        {levelsRef.current[kind] != null ? describe(kind, levelsRef.current[kind]) : ''}
      </span>
      <span
        role="button"
        aria-label={kind === KIND_STOP ? 'Remove stop loss' : 'Remove take profit'}
        onPointerDown={(event) => {
          event.stopPropagation();
          event.preventDefault();
        }}
        onClick={(event) => {
          event.stopPropagation();
          onRemove(kind);
        }}
        style={{ cursor: 'pointer', opacity: 0.85, fontWeight: 700 }}
      >
        ×
      </span>
    </div>
  );

  if (!trade) return null;

  return (
    <>
      {renderHandle(KIND_STOP, stopNodeRef)}
      {renderHandle(KIND_TARGET, targetNodeRef)}
    </>
  );
}
