import type { Status } from "../types";
import { etTime, usd } from "../format";
import Reasons from "./Reasons";

export default function Header({ status, stale, error }: { status: Status | null; stale: boolean; error: string | null }) {
  const paper = status?.mode === "PAPER" && status?.account?.paper !== false;
  const armed = !!status?.armed && !stale;
  return (
    <header className="header">
      {!paper && status && (
        <div className="live-banner">NOT A PAPER ACCOUNT. The backend refuses to trade on it.</div>
      )}
      <div className="header-row">
        <div className="brand">
          baselinetrading <span className={`badge ${paper ? "paper" : "live"}`}>{paper ? "PAPER" : "NOT PAPER"}</span>
        </div>
        <div className={`bot-state ${armed ? "armed" : "disarmed"}`}>
          Strategy {armed ? "ARMED" : "DISARMED"}
          {armed && (status?.symbols?.length ?? 0) > 0 && <span className="muted"> · watching {status?.symbols?.filter((s) => !s.decided).length} of {status?.symbols?.length} stocks for breakouts</span>}
          {armed && !(status?.symbols?.length) && status?.next_decision && <span className="muted"> · decides at {etTime(status.next_decision)} ET</span>}
        </div>
        <div className="header-facts">
          <span>{status?.symbol ?? "—"} {status?.data?.price ? usd(status.data.price) : ""}</span>
          <span className={status?.data?.ok ? "ok" : "bad"}>
            data {status?.data?.ok ? "healthy" : "not healthy"} ({status?.data?.feed ?? "?"})
          </span>
          <span className="muted">{status?.account?.number ?? ""}</span>
          <span className="muted">{etTime(status?.now)} ET</span>
        </div>
      </div>
      {stale && (
        <div className="stale-banner">
          No live update for over 10 seconds{error ? `: ${error}` : ""}. Controls are disabled until updates resume.
        </div>
      )}
      {!armed && status && <Reasons title="Strategy disarmed" reasons={status.disarmed_reasons} />}
    </header>
  );
}
