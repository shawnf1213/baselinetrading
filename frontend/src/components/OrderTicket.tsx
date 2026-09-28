import { useEffect, useState } from "react";
import { api, ApiError, type OrderResult } from "../api";
import type { Status } from "../types";
import { usd } from "../format";
import Reasons from "./Reasons";

// Manual buy with a mandatory stop. The backend's risk manager makes the real
// decision; this form only shows what it will check and why buying is disabled.
export default function OrderTicket({ status, symbol, extraReasons, onDone }: { status: Status | null; symbol: string; extraReasons: string[]; onDone: () => void }) {
  const row = status?.symbols?.find((s) => s.symbol === symbol);
  const price = row ? row.price : status?.data?.price ?? null;
  const [mode, setMode] = useState<"qty" | "notional">("qty");
  const [amount, setAmount] = useState("1");
  const [stop, setStop] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string; reasons: string[] } | null>(null);

  useEffect(() => setStop(""), [symbol]);
  useEffect(() => {
    if (price && !stop) setStop((Math.floor(price * 0.99 * 100) / 100).toFixed(2));
  }, [price, stop]);

  const amountValue = Number(amount);
  const stopValue = Number(stop);
  const qty = mode === "qty" ? amountValue : price ? amountValue / price : NaN;
  const riskUsd = price && stop ? qty * (price - stopValue) : NaN;
  const formReasons: string[] = [];
  if (!(amountValue > 0)) formReasons.push("enter a positive amount");
  if (!(stopValue > 0)) formReasons.push("every order needs a stop price");
  else if (price && stopValue >= price) formReasons.push(`the stop must be below the current price ${usd(price)}`);
  // Data health is per symbol: use the selected symbol's, not the first symbol's.
  const entryReasons = (status?.entry.reasons ?? ["no status from the backend yet"]).filter((r) => !row || !r.startsWith("market data is not healthy"));
  if (row && !row.data_ok) entryReasons.push(`market data for ${symbol} is not healthy: ${row.data_reason}`);
  if (row?.holding) entryReasons.push(`already holding ${symbol} (one position per symbol)`);
  const reasons = [...extraReasons, ...entryReasons, ...formReasons];
  const enabled = !busy && reasons.length === 0;

  const submit = async () => {
    setBusy(true);
    setResult(null);
    try {
      const body = { symbol, stop_price: stopValue, ...(mode === "qty" ? { qty: amountValue } : { notional: amountValue }) };
      setResult(await api<OrderResult>("/api/orders", body));
    } catch (e) {
      setResult(e instanceof ApiError ? { ok: false, message: e.message, reasons: e.reasons } : { ok: false, message: String(e), reasons: [] });
    } finally {
      setBusy(false);
      onDone();
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">Manual order · {symbol} · market buy + stop</div>
      <div className="toggle">
        <button className={mode === "qty" ? "on" : ""} onClick={() => setMode("qty")}>Shares</button>
        <button className={mode === "notional" ? "on" : ""} onClick={() => setMode("notional")}>Dollars</button>
      </div>
      <label>
        {mode === "qty" ? "Quantity (shares, fractions allowed)" : "Amount (USD)"}
        <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      </label>
      <label>
        Stop price (required)
        <input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} />
      </label>
      <div className="facts">
        <span>Est. value {Number.isFinite(qty) && price ? usd(qty * price) : "—"}</span>
        <span>Risk to stop {Number.isFinite(riskUsd) ? usd(riskUsd) : "—"} (max {usd(status?.limits?.max_risk_per_trade_usd)}; up to {usd(status?.account?.symbol_share ?? status?.account?.sizing_equity)} per stock)</span>
      </div>
      <button className="primary buy" disabled={!enabled} onClick={submit}>
        {busy ? "Sending…" : "Buy"}
      </button>
      <Reasons reasons={reasons} title="Buy disabled because" />
      {result && (
        <div className={result.ok ? "result ok" : "result bad"}>
          {result.message}
          {result.reasons.length > 0 && (
            <ul>
              {result.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
