import type { Status } from "../types";
import { etHm, etTime, marketOpen, routineOnly } from "../format";
import { Brand, Pill } from "./ui";

// The sticky top bar: which account, whether the bot is armed, the market, data health and the ET clock.
export default function Header({ status, stale, onLock }: { status: Status | null; stale: boolean; onLock: () => void }) {
  const paper = status?.mode === "PAPER" && status?.account?.paper !== false;
  const armed = !!status?.armed && !stale;
  const idle = routineOnly(status?.disarmed_reasons ?? []); // disarmed only by the time of day
  const symbols = status?.symbols ?? [];
  const open = marketOpen(status);
  const watching = symbols.filter((s) => !s.decided).length;
  const total = symbols.length || 1;
  const healthy = symbols.length ? symbols.filter((s) => s.data_ok).length : status?.data?.ok ? 1 : 0;
  const feed = (status?.data?.feed ?? "").toUpperCase();
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Brand />
        <div className="pills">
          {status && <Pill tone={paper ? "good" : "bad"}>{paper ? "Paper account" : "NOT A PAPER ACCOUNT"}</Pill>}
          <Pill tone={armed ? "good" : stale ? "bad" : idle ? "neutral" : "warn"} pulse={armed}>
            {armed ? (
              symbols.length ? (
                <>Armed · <b>{watching}</b> of {symbols.length} watching</>
              ) : (
                <>Armed{status?.next_decision ? ` · decides ${etHm(status.next_decision)} ET` : ""}</>
              )
            ) : stale ? (
              "No live updates"
            ) : idle ? (
              "Idle"
            ) : (
              "Disarmed"
            )}
          </Pill>
          <Pill tone={open ? "info" : "neutral"}>{open ? "Market open" : "Market closed"}</Pill>
          {open && (
            <Pill tone={healthy === total ? "good" : "warn"}>
              Data <b>{healthy}/{total}</b> {feed}
            </Pill>
          )}
          {status?.instrument === "options" && <Pill>Options · calls &amp; puts</Pill>}
        </div>
        <div className="topbar-right">
          <span className="clock">
            {etTime(status?.now)}
            <small>ET</small>
          </span>
          <span className="account-no">{status?.account?.number}</span>
          <button className="btn small" onClick={onLock} title="Forget the token on this device">
            Lock
          </button>
        </div>
      </div>
    </header>
  );
}
