import { useState } from "react";
import { api, ApiError, type OrderResult } from "../api";
import type { Position, Status } from "../types";
import { num, parseOption, plural, positionsOf, price, signedPct, stockOf, strikeAndExpiry, tone, usd } from "../format";
import Reasons from "./Reasons";
import { Card, Chip } from "./ui";

// Every open position (one per stock), each closable on its own, plus "close all".
// Closing is never blocked by risk rules; it only needs a live connection.
export default function PositionCard({ status, extraReasons, onDone, className = "" }: {
  status: Status | null;
  extraReasons: string[];
  onDone: () => void;
  className?: string;
}) {
  const positions = positionsOf(status);
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

  const blocked = busy !== null || extraReasons.length > 0;
  const total = positions.reduce((sum, p) => sum + p.unrealized_pl, 0);
  return (
    <Card
      title="Open positions"
      sub={positions.length ? plural(positions.length, "position") : "Flat"}
      right={positions.length > 0 ? <div className={`pos-pl ${tone(total)}`}>{usd(total, true)}</div> : undefined}
      className={className}
    >
      {positions.length === 0 ? (
        <div className="empty">
          <strong>No open positions</strong>
          {status?.instrument === "options" ? "Calls and puts the bot buys show up here." : "Positions show up here."}
        </div>
      ) : (
        positions.map((p) => <Row key={p.symbol} p={p} busy={busy === p.symbol} blocked={blocked} onClose={() => close(p.symbol)} />)
      )}
      {positions.length > 1 && (
        <div className="card-actions">
          <button className="btn block" disabled={blocked} onClick={() => close(null)}>
            {busy === "all" ? "Closing…" : "Close all positions"}
          </button>
        </div>
      )}
      {positions.length > 0 && <Reasons reasons={extraReasons} title="Close disabled because" />}
      {message && <div className="result">{message}</div>}
    </Card>
  );
}

function Row({ p, busy, blocked, onClose }: { p: Position; busy: boolean; blocked: boolean; onClose: () => void }) {
  const option = parseOption(p.symbol);
  const stock = stockOf(p);
  const cost = p.entry * p.qty * (option ? 100 : 1);
  const change = cost > 0 ? p.unrealized_pl / cost : null;
  let stop: string;
  if (option) {
    const sign = option.kind === "Put" ? "≥" : "≤";
    stop = p.stop_underlying != null ? `Sells if ${stock} ${sign} ${price(p.stop_underlying)}` : p.stop_note ? `Sells if ${p.stop_note}` : "No stop";
  } else {
    stop = p.stop === null ? "NO STOP" : `Stop at ${price(p.stop)}`;
  }
  return (
    <div className="pos">
      <div>
        <div className="pos-name">
          {stock}
          {option && <Chip tone={option.kind === "Call" ? "good" : "bad"}>{option.kind}</Chip>}
          {option && <span className="contract">{strikeAndExpiry(option)}</span>}
        </div>
        <div className="pos-meta">
          {option
            ? `${plural(p.qty, "contract")} · paid ${price(p.entry)} · now ${price(p.price)}`
            : `${num(p.qty, 4)} shares · entry ${price(p.entry)} · now ${price(p.price)}`}
        </div>
        <div className={`pos-stop${!option && p.stop === null ? " bad" : ""}`} title={p.source}>
          {stop} · {p.source}
        </div>
      </div>
      <div className="pos-side">
        <div className={`pos-pl ${tone(p.unrealized_pl)}`}>{usd(p.unrealized_pl, true)}</div>
        <div className="pos-pct">{signedPct(change, 1)}</div>
        <button className="btn small" disabled={blocked} onClick={onClose}>
          {busy ? "Closing…" : "Close"}
        </button>
      </div>
    </div>
  );
}
