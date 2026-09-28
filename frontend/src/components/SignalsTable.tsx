import type { JournalEvent } from "../types";
import { num, pct } from "../format";

type Inputs = Record<string, number | string | null>;
const n = (v: unknown) => (typeof v === "number" ? v : undefined);

// Every strategy decision with the inputs behind it, not just buy / no trade.
// Breakout decisions (with a symbol) and 15:30 decisions have different inputs.
export default function SignalsTable({ signals }: { signals: JournalEvent[] }) {
  const breakout = signals.some((s) => s.symbol) || signals.length === 0;
  return (
    <div>
      <div className="panel-title">Strategy decisions (last {signals.length})</div>
      <table>
        <thead>
          {breakout ? (
            <tr>
              <th>Date</th><th>Symbol</th><th>Decision</th><th>Reason</th><th>Range</th><th>Range high</th>
              <th>Range low (stop)</th><th>Range %</th><th>Breakout</th>
            </tr>
          ) : (
            <tr>
              <th>Date</th><th>Decision</th><th>Reason</th><th>Prev close</th><th>10:00 price</th><th>Morning return</th>
              <th>15:29 price</th><th>15:00–15:30 range</th><th>Volume (sig / entry)</th>
            </tr>
          )}
        </thead>
        <tbody>
          {signals.length === 0 && (
            <tr><td colSpan={9} className="muted">No decisions yet. Each stock gets one decision a day: a buy on its breakout, or no trade by 15:44 ET.</td></tr>
          )}
          {signals.map((s) => {
            const i = (s.inputs ?? null) as Inputs | null;
            if (breakout) {
              return (
                <tr key={`${s.ts}-${String(s.symbol ?? "")}`}>
                  <td>{String(s.date ?? s.ts.slice(0, 10))}</td>
                  <td><strong>{String(s.symbol ?? "—")}</strong></td>
                  <td className={s.action === "BUY" ? "up" : ""}>{String(s.action)}</td>
                  <td className="reason">{String(s.reason)}</td>
                  <td>{s.range_minutes ? `${String(s.range_minutes)} min` : "—"}</td>
                  <td>{num(n(i?.range_high))}</td>
                  <td>{num(n(i?.range_low))}</td>
                  <td>{i && n(i.range_pct) !== undefined ? `${num(n(i.range_pct), 3)}%` : "—"}</td>
                  <td>{i?.breakout_time ? `${String(i.breakout_time)} @ ${num(n(i.breakout_close))}` : "—"}</td>
                </tr>
              );
            }
            return (
              <tr key={s.ts}>
                <td>{String(s.date ?? s.ts.slice(0, 10))}</td>
                <td className={s.action === "BUY" ? "up" : ""}>{String(s.action)}</td>
                <td className="reason">{String(s.reason)}</td>
                <td>{num(n(i?.previous_close))}</td>
                <td>{num(n(i?.signal_price))}</td>
                <td>{i ? pct(n(i.signal_return) ?? NaN, 3) : "—"}</td>
                <td>{num(n(i?.reference_price))}</td>
                <td>{i ? `${num(n(i.entry_window_range_pct), 3)}%` : "—"}</td>
                <td>{i ? `${Math.round(n(i.signal_window_volume) ?? 0).toLocaleString()} / ${Math.round(n(i.entry_window_volume) ?? 0).toLocaleString()}` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
