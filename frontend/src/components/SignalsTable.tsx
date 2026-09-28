import type { JournalEvent } from "../types";
import { num, pct } from "../format";

type Inputs = Record<string, number>;

// Every strategy decision with the inputs behind it, not just buy / no trade.
export default function SignalsTable({ signals }: { signals: JournalEvent[] }) {
  return (
    <div>
      <div className="panel-title">Strategy decisions (last {signals.length})</div>
      <table>
        <thead>
          <tr>
            <th>Date</th><th>Decision</th><th>Reason</th><th>Prev close</th><th>10:00 price</th><th>Morning return</th>
            <th>15:29 price</th><th>15:00–15:30 range</th><th>Volume (sig / entry)</th>
          </tr>
        </thead>
        <tbody>
          {signals.length === 0 && (
            <tr><td colSpan={9} className="muted">No decisions yet. The strategy decides once a day at 15:30 ET.</td></tr>
          )}
          {signals.map((s) => {
            const i = (s.inputs ?? null) as Inputs | null;
            return (
              <tr key={s.ts}>
                <td>{String(s.date ?? s.ts.slice(0, 10))}</td>
                <td className={s.action === "BUY" ? "up" : ""}>{String(s.action)}</td>
                <td className="reason">{String(s.reason)}</td>
                <td>{num(i?.previous_close)}</td>
                <td>{num(i?.signal_price)}</td>
                <td>{i ? pct(i.signal_return, 3) : "—"}</td>
                <td>{num(i?.reference_price)}</td>
                <td>{i ? `${num(i.entry_window_range_pct, 3)}%` : "—"}</td>
                <td>{i ? `${Math.round(i.signal_window_volume).toLocaleString()} / ${Math.round(i.entry_window_volume).toLocaleString()}` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
