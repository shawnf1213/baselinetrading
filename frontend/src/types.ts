// Mirrors Engine.build_status() in src/baselinetrading/engine.py.

export interface JournalEvent {
  ts: string;
  kind: string;
  source: string;
  [key: string]: unknown;
}

export interface SourcePnl {
  trades: number;
  wins: number;
  gross_pnl: number;
  costs: number;
  net_pnl: number;
  win_rate: number | null;
  win_rate_ci: [number, number] | null;
}

export interface Status {
  state: string;
  mode: string;
  now?: string;
  symbol?: string;
  armed: boolean;
  disarmed_reasons: string[];
  next_decision?: string | null;
  engine_error?: string | null;
  kill_switch?: boolean;
  entry: { enabled: boolean; reasons: string[] };
  data?: { ok: boolean; reason: string; price: number | null; last_bar_end: string | null; feed: string };
  account?: {
    number: string;
    paper: boolean;
    equity: number;
    cash: number;
    sizing_equity: number;
    day_pnl: number;
    daily_loss_limit: number;
  };
  limits?: { max_risk_per_trade_usd: number; entries_today: number; max_entries_per_day: number };
  position?: {
    symbol: string;
    qty: number;
    entry: number;
    price: number;
    unrealized_pl: number;
    stop: number | null;
    source: string;
  } | null;
  pnl_today?: { manual: SourcePnl; strategy: SourcePnl };
  breakeven?: { round_trip_cost_usd: number; round_trip_cost_bps: number; notional_usd: number } | null;
  signals: JournalEvent[];
  events: JournalEvent[];
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
