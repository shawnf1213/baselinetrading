import type { SourcePnl, Status } from "../types";
import { num, pct, tone, usd } from "../format";
import { Card } from "./ui";

// Today's closed trades, split by who placed them, net of modelled costs.
export default function PnlCard({ status, className = "" }: { status: Status | null; className?: string }) {
  const rows: [string, SourcePnl | undefined][] = [
    ["Strategy", status?.pnl_today?.strategy],
    ["Manual", status?.pnl_today?.manual],
  ];
  const be = status?.breakeven;
  return (
    <Card title="Closed trades today" sub="by who placed them" className={className}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th className="num">Trades</th>
              <th className="num">Net</th>
              <th className="num">Win rate</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, r]) => (
              <tr key={name}>
                <td>
                  {name}
                  <span className="sub">
                    gross {usd(r?.gross_pnl, true)} · costs {usd(r?.costs)}
                  </span>
                </td>
                <td className="num">{r?.trades ?? 0}</td>
                <td className={`num ${tone(r?.net_pnl)}`}>{usd(r?.net_pnl, true)}</td>
                <td className="num">
                  {pct(r?.win_rate, 0)}
                  {r?.win_rate_ci && <span className="sub">{`${pct(r.win_rate_ci[0], 0)}–${pct(r.win_rate_ci[1], 0)}`}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="card-foot">
        Paper fills charge nothing, so modelled costs are taken off. The range under a win rate is its 95% interval: one day is
        far too few trades to judge an edge.
      </p>
      {status?.instrument !== "options" && be && (
        <p className="card-foot">
          Breakeven bar: one strategy round trip costs {usd(be.round_trip_cost_usd)} ({num(be.round_trip_cost_bps, 1)} bp).
        </p>
      )}
    </Card>
  );
}
