import type { Status } from "../types";
import { plural, positionsOf, signedPct, tone, usd, usd0 } from "../format";

// The day at a glance: one hero figure (the account's P&L today), then four tiles.
export default function StatsStrip({ status }: { status: Status | null }) {
  const account = status?.account;
  const positions = positionsOf(status);
  const open = positions.reduce((sum, p) => sum + p.unrealized_pl, 0);
  const s = status?.pnl_today?.strategy;
  const m = status?.pnl_today?.manual;
  const closedNet = (s?.net_pnl ?? 0) + (m?.net_pnl ?? 0);
  const trades = (s?.trades ?? 0) + (m?.trades ?? 0);
  const wins = (s?.wins ?? 0) + (m?.wins ?? 0);
  const limits = status?.limits;
  const perStock = status?.symbols?.[0]?.max_entries;
  const start = account ? account.equity - account.day_pnl : null;
  const dayPct = account && start && start > 0 ? account.day_pnl / start : null;
  const used = account ? Math.max(0, -account.day_pnl) : 0;
  const share = account && account.daily_loss_limit > 0 ? Math.min(1, used / account.daily_loss_limit) : 0;
  const severity = share >= 0.8 ? "bad" : share >= 0.5 ? "warn" : "";
  return (
    <section className="card stats" aria-label="Today at a glance">
      <div className="stat hero">
        <div className="eyebrow">
          <span className="dot" />
          Today · account P&amp;L
        </div>
        <div className={`hero-value ${tone(account?.day_pnl)}`}>{usd(account?.day_pnl, true)}</div>
        <div className="stat-sub">
          {signedPct(dayPct)} · equity {usd(account?.equity)}
        </div>
      </div>
      <div className="stat">
        <div className={`stat-value ${tone(open)}`}>{usd(open, true)}</div>
        <div className="stat-label">Open P&amp;L</div>
        <div className="stat-sub">{plural(positions.length, "open position")}</div>
      </div>
      <div className="stat">
        <div className={`stat-value ${tone(closedNet)}`}>{usd(closedNet, true)}</div>
        <div className="stat-label">Closed today, net</div>
        <div className="stat-sub">
          {plural(trades, "trade")} · {plural(wins, "win")}
        </div>
      </div>
      <div className="stat">
        <div className="stat-value">
          {limits?.entries_today ?? "—"}
          <span className="stat-of"> / {limits?.max_entries_per_day ?? "—"}</span>
        </div>
        <div className="stat-label">Entries today</div>
        <div className="stat-sub">{perStock ? `up to ${perStock} per stock` : "account limit"}</div>
      </div>
      <div className="stat">
        <div className="stat-value">
          {usd0(used)}
          <span className="stat-of"> / {usd0(account?.daily_loss_limit)}</span>
        </div>
        <div className="stat-label">Daily loss limit used</div>
        <div className={`meter ${severity}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}>
          <span style={{ width: `${share * 100}%` }} />
        </div>
      </div>
    </section>
  );
}
