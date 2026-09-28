import { useState } from "react";
import { api, ApiError, type OrderResult } from "../api";
import type { Status } from "../types";

// Two steps, typed confirmation: the kill switch cancels every order and flattens every position.
export default function KillSwitch({ status, onDone }: { status: Status | null; onDone: () => void }) {
  const [confirming, setConfirming] = useState<null | "FLATTEN" | "RESET">(null);
  const [typed, setTyped] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const engaged = !!status?.kill_switch;

  const fire = async () => {
    const path = confirming === "FLATTEN" ? "/api/kill" : "/api/kill/reset";
    try {
      const r = await api<OrderResult>(path, { confirm: typed, reason: "kill switch from the UI" });
      setMessage(r.message);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      setConfirming(null);
      setTyped("");
      onDone();
    }
  };

  return (
    <div className={`panel kill ${engaged ? "engaged" : ""}`}>
      <div className="panel-title">Kill switch {engaged && <span className="bad">· ENGAGED</span>}</div>
      <p className="muted">
        {engaged
          ? "All orders cancelled and positions flattened. New entries are blocked until you reset it."
          : "Cancels every order, flattens every position, and blocks new entries (manual and strategy)."}
      </p>
      {confirming === null ? (
        <div className="row">
          <button className="danger" onClick={() => setConfirming("FLATTEN")}>Kill switch: flatten everything</button>
          {engaged && <button className="secondary" onClick={() => setConfirming("RESET")}>Reset</button>}
        </div>
      ) : (
        <div className="confirm">
          <label>
            Type <code>{confirming}</code> to confirm
            <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
          </label>
          <div className="row">
            <button className={confirming === "FLATTEN" ? "danger" : "primary"} disabled={typed !== confirming} onClick={fire}>
              Confirm
            </button>
            <button className="secondary" onClick={() => { setConfirming(null); setTyped(""); }}>Cancel</button>
          </div>
        </div>
      )}
      {message && <div className="result">{message}</div>}
    </div>
  );
}
