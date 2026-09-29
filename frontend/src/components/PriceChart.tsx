import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineWidth,
  type UTCTimestamp,
} from "lightweight-charts";
import { api } from "../api";
import type { Candle, Status } from "../types";
import { parseOption, positionsOf, price, signedPct, stockOf, tone } from "../format";

// Candle colors: checked as a pair against the card surface with the dataviz palette validator
// (dark mode: lightness band, color-blind separation, contrast). Levels use the status colors.
const UP = "#00a88f";
const DOWN = "#ff4444";
const RANGE = "#6b9fff";
const STOP = "#ff4d5a";
const ENTRY = "rgba(255, 255, 255, 0.7)";

const ET = (t: number) =>
  new Date(t * 1000).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
const etDay = (t: number) => new Date(t * 1000).toLocaleDateString("en-US", { timeZone: "America/New_York" });

interface Level {
  price: number;
  color: string;
  width: LineWidth;
  style: LineStyle;
  title: string;
}

// Lightweight Charts (Apache 2.0). The TradingView attribution logo stays on, as its license asks.
export default function PriceChart({ status, symbol, onSelect, className = "" }: {
  status: Status | null;
  symbol: string | null; // null until the first status arrives
  onSelect: (symbol: string) => void;
  className?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const levelPrices = useRef<number[]>([]); // kept inside the visible price range
  const [bars, setBars] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const c = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "rgba(0, 0, 0, 0)" },
        textColor: "#7a7a7a",
        fontFamily: "Barlow, system-ui, sans-serif",
        fontSize: 12,
        attributionLogo: true,
      },
      grid: { vertLines: { color: "rgba(255, 255, 255, 0.035)" }, horzLines: { color: "rgba(255, 255, 255, 0.035)" } },
      crosshair: {
        vertLine: { color: "rgba(255, 255, 255, 0.25)", labelBackgroundColor: "#1c2220" },
        horzLine: { color: "rgba(255, 255, 255, 0.25)", labelBackgroundColor: "#1c2220" },
      },
      // The page scrolls under the wheel and vertical swipes; drag to pan, pinch or drag an axis to zoom.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      rightPriceScale: { borderColor: "rgba(255, 255, 255, 0.08)", scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: { borderColor: "rgba(255, 255, 255, 0.08)", timeVisible: true, secondsVisible: false, tickMarkFormatter: (t: number) => ET(t) },
      localization: { timeFormatter: (t: number) => `${ET(t)} ET`, priceFormatter: (p: number) => p.toFixed(2) },
    });
    series.current = c.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } | null } | null) => {
        const base = original();
        if (!base?.priceRange || levelPrices.current.length === 0) return base;
        return {
          ...base,
          priceRange: {
            minValue: Math.min(base.priceRange.minValue, ...levelPrices.current),
            maxValue: Math.max(base.priceRange.maxValue, ...levelPrices.current),
          },
        };
      },
    });
    chart.current = c;
    return () => c.remove();
  }, []);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setLoading(true); // the previous symbol's chart stays up, dimmed, until the new bars arrive
    const load = async () => {
      try {
        const { bars } = await api<{ bars: Candle[] }>(`/api/bars?symbol=${encodeURIComponent(symbol)}`);
        if (cancelled || !series.current) return;
        series.current.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
        setBars(bars);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [symbol]);

  const row = status?.symbols?.find((s) => s.symbol === symbol);
  const options = status?.instrument === "options";
  const position = positionsOf(status).find((p) => stockOf(p) === symbol) ?? null;
  const option = position ? parseOption(position.symbol) : null;
  const stop = position?.stop_underlying ?? position?.stop ?? null;
  const near = (a: number, b: number | null) => b !== null && Math.abs(a - b) < 0.005;

  const levels: Level[] = [];
  if (stop !== null) {
    levels.push({ price: stop, color: STOP, width: 2, style: LineStyle.Solid, title: option ? `stop · sells the ${option.kind.toLowerCase()}` : "stop" });
  }
  if (position && !option) levels.push({ price: position.entry, color: ENTRY, width: 1, style: LineStyle.Dashed, title: `entry (${position.source})` });
  if (row?.range_high != null && !near(row.range_high, stop)) {
    levels.push({ price: row.range_high, color: RANGE, width: 1, style: LineStyle.Dashed, title: options ? "calls above" : "range high" });
  }
  if (row?.range_low != null && !near(row.range_low, stop)) {
    levels.push({ price: row.range_low, color: RANGE, width: 1, style: LineStyle.Dashed, title: options ? "puts below" : "range low" });
  }
  const levelKey = JSON.stringify(levels);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    lines.current.forEach((l) => s.removePriceLine(l));
    lines.current = levels.map((l) =>
      s.createPriceLine({ price: l.price, color: l.color, lineWidth: l.width, lineStyle: l.style, axisLabelVisible: true, title: l.title }),
    );
    levelPrices.current = levels.map((l) => l.price);
  }, [levelKey]);

  // Today's change: the last close against the previous session's last close.
  const { last, change } = useMemo(() => {
    if (bars.length === 0) return { last: null, change: null };
    const final = bars[bars.length - 1];
    const today = etDay(final.time);
    let previous: number | null = null;
    for (let i = bars.length - 1; i >= 0; i--) {
      if (etDay(bars[i].time) !== today) {
        previous = bars[i].close;
        break;
      }
    }
    return { last: final.close, change: previous ? final.close / previous - 1 : null };
  }, [bars]);
  const shown = row?.price ?? last;

  return (
    <section className={`card ${className}`}>
      <div className="chart-head">
        <div className="chart-id">
          <span className="chart-symbol">{symbol ?? "—"}</span>
          <span className="chart-price">{price(shown)}</span>
          {change !== null && <span className={`chart-change ${tone(change)}`}>{signedPct(change)} today</span>}
        </div>
        {(status?.symbols?.length ?? 0) > 1 && (
          <div className="symbol-tabs" role="tablist" aria-label="Stock shown in the chart">
            {status?.symbols?.map((s) => (
              <button key={s.symbol} role="tab" aria-selected={s.symbol === symbol} className={s.symbol === symbol ? "on" : ""} onClick={() => onSelect(s.symbol)}>
                {s.symbol}
                {s.holding && <span className="held" title="holding" />}
              </button>
            ))}
          </div>
        )}
      </div>
      {error && <Notice text={`Chart data unavailable: ${error}`} />}
      <div className={`chart${loading ? " loading" : ""}`} ref={container} />
      <div className="chart-legend">
        <span className="key"><i className="candle" style={{ background: UP }} />Up</span>
        <span className="key"><i className="candle" style={{ background: DOWN }} />Down</span>
        {row?.range_high != null && (
          <span className="key"><i style={{ color: RANGE, borderTopStyle: "dashed" }} />Opening range, {row.range_minutes} min</span>
        )}
        {stop !== null && <span className="key"><i style={{ color: STOP }} />Stop</span>}
        <span>1-minute bars, for display; decisions use validated data</span>
      </div>
    </section>
  );
}

function Notice({ text }: { text: string }) {
  return (
    <div className="callout info" style={{ marginBottom: 12 }}>
      <ul><li>{text}</li></ul>
    </div>
  );
}
