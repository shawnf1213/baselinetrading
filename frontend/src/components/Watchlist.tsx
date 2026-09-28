import type { Status } from "../types";
import { usd } from "../format";

// Every traded symbol: its price, opening range length, data health and what the breakout is doing.
// Clicking a row shows that symbol in the chart and the order ticket.
export default function Watchlist({ status, selected, onSelect }: { status: Status | null; selected: string; onSelect: (s: string) => void }) {
  const rows = status?.symbols ?? [];
  if (rows.length === 0) return null;
  return (
    <div>
      <div className="panel-title">
        Watchlist · {rows.length} stocks · {usd(status?.account?.symbol_share)} each
      </div>
      <table>
        <thead>
          <tr><th>Symbol</th><th>Price</th><th>Range</th><th>Data</th><th>Position</th><th>Breakout</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol} className={r.symbol === selected ? "selected" : ""} onClick={() => onSelect(r.symbol)} style={{ cursor: "pointer" }}>
              <td><strong>{r.symbol}</strong></td>
              <td>{r.price ? usd(r.price) : "—"}</td>
              <td>{r.range_minutes} min</td>
              <td className={r.data_ok ? "ok" : "bad"} title={r.data_reason}>{r.data_ok ? "ok" : "gap/stale"}</td>
              <td className={r.holding ? "up" : "muted"}>{r.holding ? "holding" : "flat"}</td>
              <td className="reason">{r.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
