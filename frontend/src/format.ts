import type { Position, Status } from "./types";

const finite = (value: number | null | undefined): value is number => value !== null && value !== undefined && Number.isFinite(value);

export const usd = (value: number | null | undefined, signed = false): string => {
  if (!finite(value)) return "—";
  const text = Math.abs(value).toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return value < 0 ? `-${text}` : text;
  return value === 0 ? text : `${value < 0 ? "−" : "+"}${text}`;
};

// Whole dollars, for limits and shares of the account.
export const usd0 = (value: number | null | undefined): string =>
  finite(value) ? value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }) : "—";

export const num = (value: number | null | undefined, digits = 2): string => (finite(value) ? value.toFixed(digits) : "—");

// A price with thousands separators: 1,053.98.
export const price = (value: number | null | undefined): string =>
  finite(value) ? value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";

export const pct = (value: number | null | undefined, digits = 1): string => (finite(value) ? `${(value * 100).toFixed(digits)}%` : "—");

export const signedPct = (value: number | null | undefined, digits = 2): string =>
  finite(value) ? `${value < 0 ? "−" : "+"}${Math.abs(value * 100).toFixed(digits)}%` : "—";

export const etTime = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false }) : "—";

export const etHm = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";

export const tone = (value: number | null | undefined): string => (!finite(value) || value === 0 ? "" : value > 0 ? "up" : "down");

export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;

// OCC option symbols: root, yymmdd expiry, C or P, strike x 1000 in 8 digits.
const OCC = /^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;

export interface OptionContract {
  stock: string;
  expiry: Date;
  kind: "Call" | "Put";
  strike: number;
}

export const parseOption = (symbol: string): OptionContract | null => {
  const m = OCC.exec(symbol);
  if (!m) return null;
  const [, stock, yy, mm, dd, cp, strike] = m;
  return {
    stock,
    expiry: new Date(Date.UTC(2000 + Number(yy), Number(mm) - 1, Number(dd))),
    kind: cp === "C" ? "Call" : "Put",
    strike: Number(strike) / 1000,
  };
};

// "230 strike · Oct 9"
export const strikeAndExpiry = (c: OptionContract): string =>
  `${Number.isInteger(c.strike) ? c.strike.toFixed(0) : c.strike} strike · ${c.expiry.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}`;

// The stock a position is on, whether held as shares or as an option.
export const stockOf = (p: Position): string => p.underlying ?? parseOption(p.symbol)?.stock ?? p.symbol;

export const positionsOf = (status: Status | null): Position[] => status?.positions ?? (status?.position ? [status.position] : []);

export const marketOpen = (status: Status | null): boolean => {
  const s = status?.session;
  if (!s || !status?.now) return false;
  const now = new Date(status.now).getTime();
  return new Date(s.open).getTime() <= now && now < new Date(s.close).getTime();
};

// Reasons the bot is disarmed that are just the time of day, not a problem.
const ROUTINE = ["market is closed", "market hasn't opened", "every symbol's decision for today is made", "no new entries in the last", "half day"];
export const routineOnly = (reasons: string[]): boolean => reasons.length > 0 && reasons.every((r) => ROUTINE.some((p) => r.startsWith(p)));

// Engine action names, as people say them.
export const actionLabel = (action: string, options: boolean): string =>
  action === "BUY_PUT" ? "Put" : action === "BUY" ? (options ? "Call" : "Buy") : action === "NO_TRADE" ? "No trade" : action;

export const actionTone = (action: string): "good" | "bad" | "neutral" => (action === "BUY" ? "good" : action === "BUY_PUT" ? "bad" : "neutral");
