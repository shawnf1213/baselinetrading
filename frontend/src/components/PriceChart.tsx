import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { api } from "../api";
import type { Candle, Status } from "../types";

const ET = (t: number) =>
  new Date(t * 1000).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });

// Lightweight Charts (Apache 2.0). The TradingView attribution logo stays on, as its license asks.
export default function PriceChart({ status }: { status: Status | null }) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const levels = useRef<number[]>([]); // entry and stop, kept inside the visible price range
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const c = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#0f1318" }, textColor: "#9aa7b4", attributionLogo: true },
      grid: { vertLines: { color: "#1b222b" }, horzLines: { color: "#1b222b" } },
      timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: (t: number) => ET(t) },
      localization: { timeFormatter: (t: number) => `${ET(t)} ET` },
      rightPriceScale: { borderColor: "#27303b" },
    });
    series.current = c.addSeries(CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350", borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } | null } | null) => {
        const base = original();
        if (!base?.priceRange || levels.current.length === 0) return base;
        return {
          ...base,
          priceRange: {
            minValue: Math.min(base.priceRange.minValue, ...levels.current),
            maxValue: Math.max(base.priceRange.maxValue, ...levels.current),
          },
        };
      },
    });
    chart.current = c;
    return () => c.remove();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const { bars } = await api<{ bars: Candle[] }>("/api/bars");
        if (cancelled || !series.current) return;
        series.current.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // Entry and stop of the open position as price lines.
  const position = status?.position;
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    lines.current.forEach((l) => s.removePriceLine(l));
    lines.current = [];
    levels.current = position ? [position.entry, ...(position.stop !== null ? [position.stop] : [])] : [];
    if (!position) return;
    lines.current.push(
      s.createPriceLine({ price: position.entry, color: "#4c8dff", lineWidth: 1, lineStyle: LineStyle.Dashed, title: `entry (${position.source})` }),
    );
    if (position.stop !== null) {
      lines.current.push(s.createPriceLine({ price: position.stop, color: "#ef5350", lineWidth: 2, lineStyle: LineStyle.Solid, title: "stop" }));
    }
  }, [position?.entry, position?.stop, position?.source]);

  return (
    <div className="chart-wrap">
      <div className="panel-title">
        {status?.symbol ?? "SPY"} · 1 minute · display only (decisions use validated data)
      </div>
      {error && <div className="reasons">Chart data unavailable: {error}</div>}
      <div className="chart" ref={container} />
    </div>
  );
}
