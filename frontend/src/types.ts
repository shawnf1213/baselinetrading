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

export interface Position {
  symbol: string; // the stock, or an OCC option symbol such as NVDA261009C00230000
  underlying?: string; // the stock an option is on
  qty: number;
  entry: number;
  price: number;
  unrealized_pl: number;
  stop: number | null; // a broker stop order (shares)
  stop_underlying?: number | null; // options: the stock level at which the bot sells
  stop_note?: string | null; // options: the bot-managed stop on the stock, as text
  source: string;
}

// One traded symbol's breakout state (empty for the single-symbol strategy).
export interface SymbolState {
  symbol: string;
  range_minutes: number;
  range_high?: number | null; // today's opening range, once it is complete
  range_low?: number | null;
  entries?: number; // breakout entries today
  max_entries?: number;
  price: number | null;
  data_ok: boolean;
  data_reason: string;
  decided: boolean;
  status: string;
  holding: boolean;
}

export interface Status {
  state: string;
  mode: string;
  now?: string;
  symbol?: string;
  strategy?: string;
  instrument?: string; // "shares" or "options"
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
    symbol_share?: number;
    day_pnl: number;
    daily_loss_limit: number;
  };
  limits?: { max_risk_per_trade_usd: number; entries_today: number; max_entries_per_day: number };
  position?: Position | null;
  positions?: Position[];
  session?: { open: string; close: string } | null;
  symbols?: SymbolState[];
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
