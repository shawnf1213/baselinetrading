import { useState } from "react";
import { api, ApiError, type OrderResult } from "../api";
import type { Status } from "../types";
import { num, tone, usd } from "../format";
import Reasons from "./Reasons";

export default function PositionCard({ status, extraReasons, onDone }: { status: Status | null; extraReasons: string[]; onDone: () => void }) {
  const p = status?.position;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const close = async () => {
    setBusy(true);
    try {
      const r = await api<OrderResult>("/api/positions/close", { reason: "manual close from the UI" });
      setMessage(r.message);
    } catch (e) {
      setMessage(e instanceof ApiError ? `${e.message} ${e.reasons.join("; ")}` : String(e));
    } finally {
      setBusy(false);
      onDone();
    }
  };

  // Closing is never blocked by risk rules; it only needs a position and a live connection.
  const reasons = [...extraReasons, ...(p ? [] : ["no open position"])];
  return (
    <div className="panel">
      <div className="panel-title">Open position</div>
      {p ? (
        <dl className="kv">
          <dt>Symbol</dt><dd>{p.symbol} · {p.source}</dd>
          <dt>Quantity</dt><dd>{num(p.qty, 4)}</dd>
          <dt>Entry</dt><dd>{usd(p.entry)}</dd>
          <dt>Stop</dt><dd className={p.stop === null ? "bad" : ""}>{p.stop === null ? "NONE (will be flattened)" : usd(p.stop)}</dd>
          <dt>Last</dt><dd>{usd(p.price)}</dd>
          <dt>Unrealised</dt><dd className={tone(p.unrealized_pl)}>{usd(p.unrealized_pl, true)}</dd>
        </dl>
      ) : (
        <p className="muted">Flat.</p>
      )}
      <button className="secondary" disabled={busy || reasons.length > 0} onClick={close}>
        {busy ? "Closing…" : "Close position"}
      </button>
      {p && <Reasons reasons={extraReasons} title="Close disabled because" />}
      {message && <div className="result">{message}</div>}
    </div>
  );
}
