import { Fragment } from "react";
import type { Position, Status, SymbolState } from "../types";
import { actionLabel, marketOpen, positionsOf, price, stockOf, usd0 } from "../format";
import { Card, Chip, type Tone } from "./ui";

// Every traded stock: its price, opening range, state and what the bot will do next.
// Clicking a row shows that stock in the chart (and the order ticket).
export default function Watchlist({ status, selected, onSelect, className = "" }: {
  status: Status | null;
  selected: string;
  onSelect: (symbol: string) => void;
  className?: string;
}) {
  const rows = status?.symbols ?? [];
  if (rows.length === 0) return null;
  const open = marketOpen(status);
  const options = status?.instrument === "options";
  const positions = positionsOf(status);
  return (
    <Card title="Watchlist" sub={`${rows.length} stocks · ${usd0(status?.account?.symbol_share)} each · select one to chart it`} className={className}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Stock</th>
              <th className="num">Last</th>
              <th className="hide-sm">Opening range</th>
              <th>State</th>
              <th className="num hide-sm">Entries</th>
              <th className="hide-sm">Next</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const [tone, label] = stateOf(r, open);
              const text = next(r, open, options, positions);
              const pick = `pick${r.symbol === selected ? " selected" : ""}`;
              return (
                <Fragment key={r.symbol}>
                  <tr
                    className={`${pick} has-detail`}
                    onClick={() => onSelect(r.symbol)}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(r.symbol)}
                    tabIndex={0}
                    aria-selected={r.symbol === selected}
                  >
                    <td className="sym">{r.symbol}</td>
                    <td className="num">{price(r.price)}</td>
                    <td className="hide-sm dim num" style={{ textAlign: "left" }}>
                      {r.range_high != null && r.range_low != null ? `${price(r.range_low)} – ${price(r.range_high)}` : "—"}
                      <span className="sub">{r.range_minutes} min</span>
                    </td>
                    <td><Chip tone={tone} title={r.data_ok ? undefined : r.data_reason}>{label}</Chip></td>
                    <td className="num hide-sm dim">{r.entries ?? 0}/{r.max_entries ?? "—"}</td>
                    <td className="wrap dim small hide-sm">{text}</td>
                  </tr>
                  <tr className={`${pick} detail-row`} onClick={() => onSelect(r.symbol)} aria-hidden>
                    <td colSpan={6} className="dim small">{text}</td>
                  </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function stateOf(r: SymbolState, open: boolean): [Tone, string] {
  if (r.holding) return ["good", "Holding"];
  if (open && !r.data_ok) return ["warn", "Data gap"];
  if (r.decided) return ["neutral", "Done for today"];
  return open ? ["info", "Watching"] : ["neutral", "Waiting"];
}

// What happens next for this stock, in plain words; falls back to the engine's own status.
function next(r: SymbolState, open: boolean, options: boolean, positions: Position[]): string {
  if (r.holding) {
    const p = positions.find((q) => stockOf(q) === r.symbol);
    if (p?.stop_underlying != null) return `Sells if ${r.symbol} ${p.stop_note?.includes("≥") ? "≥" : "≤"} ${price(p.stop_underlying)}`;
    if (p?.stop != null) return `Stop at ${price(p.stop)}`;
    return r.status;
  }
  if (open && !r.data_ok) return r.data_reason || r.status;
  if (!r.decided && open && r.range_high != null && r.range_low != null) {
    return options
      ? `Call above ${price(r.range_high)} · put below ${price(r.range_low)}`
      : `Buys above ${price(r.range_high)} (stop ${price(r.range_low)})`;
  }
  const m = /^(BUY_PUT|BUY|NO_TRADE): (.*)$/.exec(r.status);
  return m ? `${actionLabel(m[1], options)} · ${m[2]}` : r.status;
}
