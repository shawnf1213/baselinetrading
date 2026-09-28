import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, getToken, setToken, statusSocket } from "./api";
import type { Status } from "./types";
import Header from "./components/Header";
import PriceChart from "./components/PriceChart";
import OrderTicket from "./components/OrderTicket";
import PositionCard from "./components/PositionCard";
import PnlCard from "./components/PnlCard";
import SignalsTable from "./components/SignalsTable";
import EventLog from "./components/EventLog";
import KillSwitch from "./components/KillSwitch";
import Watchlist from "./components/Watchlist";

const STALE_AFTER_MS = 10_000;

export default function App() {
  const [token, setTokenState] = useState<string | null>(getToken());
  const [status, setStatus] = useState<Status | null>(null);
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [picked, setPicked] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api<Status>("/api/status"));
      setLastUpdate(Date.now());
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setToken(null);
        setTokenState(null);
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

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
          if (!closed) setTimeout(connect, 3000); // reconnect; the stale banner shows meanwhile
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

  if (!token) return <Login onToken={(t) => { setToken(t); setTokenState(t); }} />;

  const selected = picked ?? status?.symbol ?? "SPY";
  const stale = !lastUpdate || now - lastUpdate > STALE_AFTER_MS;
  // If the UI isn't receiving updates, it can't know the bot's state, so it must not offer to trade.
  const uiReasons = stale ? ["this page is not receiving live updates from the backend"] : [];

  return (
    <div className="app">
      <Header status={status} stale={stale} error={error} />
      <main className="grid">
        <section className="panel chart-panel">
          <PriceChart status={status} symbol={selected} />
        </section>
        <aside className="side">
          <OrderTicket status={status} symbol={selected} extraReasons={uiReasons} onDone={refresh} />
          <PositionCard status={status} extraReasons={uiReasons} onDone={refresh} />
          <KillSwitch status={status} onDone={refresh} />
        </aside>
        {(status?.symbols?.length ?? 0) > 0 && (
          <section className="panel wide">
            <Watchlist status={status} selected={selected} onSelect={setPicked} />
          </section>
        )}
        <section className="panel">
          <PnlCard status={status} />
        </section>
        <section className="panel wide">
          <SignalsTable signals={status?.signals ?? []} />
        </section>
        <section className="panel wide">
          <EventLog events={status?.events ?? []} />
        </section>
      </main>
    </div>
  );
}

function Login({ onToken }: { onToken: (token: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="login">
      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onToken(value.trim());
        }}
      >
        <h1>baselinetrading</h1>
        <p className="muted">Enter the access token set in BASELINE_UI_TOKEN on the server.</p>
        <input type="password" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="token" />
        <button className="primary" type="submit">Unlock</button>
      </form>
    </div>
  );
}
