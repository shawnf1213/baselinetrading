import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, getToken, setToken, statusSocket } from "./api";
import type { Status } from "./types";
import { routineOnly, usd } from "./format";
import Header from "./components/Header";
import StatsStrip from "./components/StatsStrip";
import PriceChart from "./components/PriceChart";
import OrderTicket from "./components/OrderTicket";
import PositionCard from "./components/PositionCard";
import PnlCard from "./components/PnlCard";
import SignalsTable from "./components/SignalsTable";
import EventLog from "./components/EventLog";
import KillSwitch from "./components/KillSwitch";
import Watchlist from "./components/Watchlist";
import Reasons from "./components/Reasons";
import { Brand, Guard, Pill } from "./components/ui";

const STALE_AFTER_MS = 10_000;

export default function App() {
  const [token, setTokenState] = useState<string | null>(getToken());
  const [status, setStatus] = useState<Status | null>(null);
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [picked, setPicked] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  const lock = useCallback((wasRefused = false) => {
    setToken(null);
    setTokenState(null);
    setStatus(null);
    setRefused(wasRefused);
  }, []);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api<Status>("/api/status"));
      setLastUpdate(Date.now());
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) lock(true);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [lock]);

  useEffect(() => {
    if (!token) return;
    let closed = false;
    const connect = () => {
      socketRef.current = statusSocket(
        (s) => {
          setStatus(s as Status);
          setLastUpdate(Date.now());
        },
        () => {
          if (!closed) setTimeout(connect, 3000); // reconnect; the stale notice shows meanwhile
        },
      );
    };
    refresh();
    connect();
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      closed = true;
      socketRef.current?.close();
      clearInterval(tick);
    };
  }, [token, refresh]);

  const dayPnl = status?.account?.day_pnl;
  useEffect(() => {
    document.title = dayPnl === undefined ? "Baseline Trading" : `${usd(dayPnl, true)} · Baseline Trading`;
  }, [dayPnl]);

  if (!token) {
    return (
      <Login
        refused={refused}
        onToken={(t) => {
          setToken(t);
          setTokenState(t);
          setRefused(false);
        }}
      />
    );
  }

  const selected = picked ?? status?.symbol ?? status?.symbols?.[0]?.symbol ?? null; // none until the first status
  const stale = !lastUpdate || now - lastUpdate > STALE_AFTER_MS;
  // If the UI isn't receiving updates, it can't know the bot's state, so it must not offer to trade.
  const uiReasons = stale ? ["this page is not receiving live updates from the backend"] : [];
  const paper = status?.mode === "PAPER" && status?.account?.paper !== false;
  const armed = !!status?.armed && !stale;
  const disarmed = status?.disarmed_reasons ?? [];
  const routine = routineOnly(disarmed);

  return (
    <div className="app">
      <Header status={status} stale={stale} onLock={() => lock()} />
      <main className="page">
        {status && !paper && (
          <Reasons tone="bad" title="Not a paper account" reasons={["The backend refuses to trade on it."]} />
        )}
        {stale && (
          <Reasons
            tone="bad"
            title="Not receiving live updates"
            reasons={[`No update for over 10 seconds${error ? `: ${error}` : ""}.`, "Controls are disabled until updates resume."]}
          />
        )}
        {!stale && !armed && status && (
          <Reasons tone={routine ? "info" : "warn"} title={routine ? "Bot is idle" : "Bot is disarmed"} reasons={disarmed} />
        )}
        <Guard name="Today's numbers">
          <StatsStrip status={status} />
        </Guard>
        <div className="layout">
          <div className="col-main">
            <Guard name="The chart" className="order-chart">
              <PriceChart status={status} symbol={selected} onSelect={setPicked} className="order-chart" />
            </Guard>
            <Guard name="The watchlist" className="order-watch">
              <Watchlist status={status} selected={selected ?? ""} onSelect={setPicked} className="order-watch" />
            </Guard>
          </div>
          <div className="col-side">
            <Guard name="Open positions" className="order-positions">
              <PositionCard status={status} extraReasons={uiReasons} onDone={refresh} className="order-positions" />
            </Guard>
            <Guard name="Closed trades" className="order-today">
              <PnlCard status={status} className="order-today" />
            </Guard>
            <Guard name="The kill switch" className="order-kill">
              <KillSwitch status={status} onDone={refresh} className="order-kill" />
            </Guard>
            <Guard name="Manual orders" className="order-manual">
              <OrderTicket status={status} symbol={selected ?? "—"} extraReasons={uiReasons} onDone={refresh} className="order-manual" />
            </Guard>
          </div>
          <Guard name="Strategy decisions" className="span-all order-decisions">
            <SignalsTable signals={status?.signals ?? []} options={status?.instrument === "options"} className="span-all order-decisions" />
          </Guard>
          <Guard name="The journal" className="span-all order-journal">
            <EventLog events={status?.events ?? []} className="span-all order-journal" />
          </Guard>
        </div>
      </main>
      <footer className="footer">
        <span>Paper trading only · orders go through the risk manager · live data from {(status?.data?.feed ?? "iex").toUpperCase()}</span>
        <span>{status?.strategy ? status.strategy.replace(/_/g, " ") : ""}</span>
      </footer>
    </div>
  );
}

function Login({ onToken, refused }: { onToken: (token: string) => void; refused: boolean }) {
  const [value, setValue] = useState("");
  return (
    <div className="login">
      <form
        className="card login-card"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onToken(value.trim());
        }}
      >
        <Brand />
        <Pill tone="good">Private · paper trading</Pill>
        <h1 className="login-title">
          Unlock your <span className="grad">bot.</span>
        </h1>
        <p>Enter the access token set in BASELINE_UI_TOKEN on the server.</p>
        {refused && <div className="login-error">The server refused that token. Try again.</div>}
        <label className="field">
          <span className="field-label">Access token</span>
          <input type="password" autoFocus autoComplete="current-password" value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <button className="btn primary block" type="submit">
          Unlock
        </button>
      </form>
    </div>
  );
}
