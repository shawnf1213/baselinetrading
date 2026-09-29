import { useState } from "react";
import { api, ApiError, type OrderResult } from "../api";
import type { Status } from "../types";
import { Card, Chip } from "./ui";

// Two steps, typed confirmation: the kill switch cancels every order and flattens every position.
export default function KillSwitch({ status, onDone, className = "" }: { status: Status | null; onDone: () => void; className?: string }) {
  const [confirming, setConfirming] = useState<null | "FLATTEN" | "RESET">(null);
  const [typed, setTyped] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const engaged = !!status?.kill_switch;

  const cancel = () => {
    setConfirming(null);
    setTyped("");
  };
  const fire = async () => {
    const path = confirming === "FLATTEN" ? "/api/kill" : "/api/kill/reset";
    try {
      const r = await api<OrderResult>(path, { confirm: typed, reason: "kill switch from the UI" });
      setMessage(r.message);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      cancel();
      onDone();
    }
  };

  return (
    <Card title={<>Kill switch {engaged && <Chip tone="bad">Engaged</Chip>}</>} className={`${className}${engaged ? " alarm" : ""}`}>
      <p className="card-text">
        {engaged
          ? "Every order was cancelled and every position closed. New entries stay blocked until you reset it."
          : "Cancels every order, closes every position and blocks new entries, from the bot and from you."}
      </p>
      {confirming === null ? (
        <div className="row">
          <button className="btn danger" onClick={() => setConfirming("FLATTEN")}>
            Flatten everything
          </button>
          {engaged && (
            <button className="btn" onClick={() => setConfirming("RESET")}>
              Reset
            </button>
          )}
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (typed === confirming) fire();
          }}
        >
          <label className="field">
            <span className="field-label">
              Type <code>{confirming}</code> to confirm
            </span>
            <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} autoCapitalize="characters" autoComplete="off" />
          </label>
          <div className="row">
            <button type="submit" className={`btn ${confirming === "FLATTEN" ? "danger" : "primary"}`} disabled={typed !== confirming}>
              Confirm
            </button>
            <button type="button" className="btn" onClick={cancel}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {message && <div className="result">{message}</div>}
    </Card>
  );
}
