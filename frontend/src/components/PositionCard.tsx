import { useState } from "react";
import { api, ApiError, type OrderResult } from "../api";
import type { Status } from "../types";
import { num, tone, usd } from "../format";
import Reasons from "./Reasons";

// Every open position (one per symbol), each closable on its own, plus "close all".
export default function PositionCard({ status, extraReasons, onDone }: { status: Status | null; extraReasons: string[]; onDone: () => void }) {
  const positions = status?.positions ?? (status?.position ? [status.position] : []);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const close = async (symbol: string | null) => {
    setBusy(symbol ?? "all");
    try {
      const r = await api<OrderResult>("/api/positions/close", { reason: "manual close from the UI", symbol });
      setMessage(r.message);
    } catch (e) {
      setMessage(e instanceof ApiError ? `${e.message} ${e.reasons.join("; ")}` : String(e));
    } finally {
      setBusy(null);
      onDone();
    }
  };

  // Closing is never blocked by risk rules; it only needs a position and a live connection.
  const blocked = busy !== null || extraReasons.length > 0;
  const total = positions.reduce((sum, p) => sum + p.unrealized_pl, 0);
  return (
    <div className="panel">
      <div className="panel-title">
        Open positions ({positions.length}){positions.length > 0 && <span className={tone(total)}> · {usd(total, true)}</span>}
      </div>
      {positions.length === 0 ? (
        <p className="muted">Flat.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Stop</th><th>Last</th><th>P&amp;L</th><th></th></tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.symbol}>
                <td title={p.source}>{p.symbol}</td>
                <td>{num(p.qty, 4)}</td>
                <td>{usd(p.entry)}</td>
                <td className={p.stop === null && !p.stop_note ? "bad" : ""}>{p.stop_note ?? (p.stop === null ? "NONE" : usd(p.stop))}</td>
                <td>{usd(p.price)}</td>
                <td className={tone(p.unrealized_pl)}>{usd(p.unrealized_pl, true)}</td>
                <td>
                  <button className="secondary" disabled={blocked} onClick={() => close(p.symbol)}>
                    {busy === p.symbol ? "…" : "Close"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button className="secondary" disabled={blocked || positions.length === 0} onClick={() => close(null)}>
        {busy === "all" ? "Closing…" : "Close all positions"}
      </button>
      {positions.length > 0 && <Reasons reasons={extraReasons} title="Close disabled because" />}
      {message && <div className="result">{message}</div>}
    </div>
  );
}
