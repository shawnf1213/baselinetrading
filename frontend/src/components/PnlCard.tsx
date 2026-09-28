import type { SourcePnl, Status } from "../types";
import { num, pct, tone, usd } from "../format";

// Today's P&L, split by who placed the trade, measured against the cost of a round trip.
export default function PnlCard({ status }: { status: Status | null }) {
  const account = status?.account;
  const be = status?.breakeven;
  const rows: [string, SourcePnl | undefined][] = [
    ["Strategy", status?.pnl_today?.strategy],
    ["Manual", status?.pnl_today?.manual],
  ];
  const used = account ? Math.min(1, Math.max(0, -account.day_pnl / account.daily_loss_limit)) : 0;
  return (
    <div>
      <div className="panel-title">Today</div>
      <div className="stats">
        <div>
          <div className="label">Account day P&amp;L (broker)</div>
          <div className={`value ${tone(account?.day_pnl)}`}>{usd(account?.day_pnl, true)}</div>
        </div>
        <div>
          <div className="label">Daily loss limit</div>
          <div className="value">−{usd(account?.daily_loss_limit)}</div>
          <div className="meter"><span style={{ width: `${used * 100}%` }} /></div>
        </div>
        <div>
          <div className="label">Breakeven bar: cost of one strategy round trip</div>
          <div className="value">{be ? `${usd(be.round_trip_cost_usd)} (${num(be.round_trip_cost_bps, 1)} bp)` : "—"}</div>
        </div>
        <div>
          <div className="label">Entries today</div>
          <div className="value">{status?.limits ? `${status.limits.entries_today} / ${status.limits.max_entries_per_day}` : "—"}</div>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Source</th><th>Trades</th><th>Gross</th><th>Modelled costs</th><th>Net</th><th>Win rate (95% CI)</th></tr>
        </thead>
        <tbody>
          {rows.map(([name, r]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{r?.trades ?? 0}</td>
              <td className={tone(r?.gross_pnl)}>{usd(r?.gross_pnl, true)}</td>
              <td>{usd(r?.costs)}</td>
              <td className={tone(r?.net_pnl)}>{usd(r?.net_pnl, true)}</td>
              <td>{r?.win_rate_ci ? `${pct(r.win_rate)} (${pct(r.win_rate_ci[0], 0)}–${pct(r.win_rate_ci[1], 0)})` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Paper fills charge no fees, so modelled fees and slippage are subtracted. A day's results are far too few trades to judge
        an edge (hundreds are needed); see docs/methodology.md.
      </p>
    </div>
  );
}
