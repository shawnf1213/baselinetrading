export const usd = (value: number | null | undefined, signed = false): string => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const text = Math.abs(value).toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return value < 0 ? `-${text}` : text;
  return `${value < 0 ? "−" : "+"}${text}`;
};

export const num = (value: number | null | undefined, digits = 2): string =>
  value === null || value === undefined || !Number.isFinite(value) ? "—" : value.toFixed(digits);

export const pct = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

export const etTime = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false }) : "—";

export const tone = (value: number | null | undefined): string =>
  value === null || value === undefined || value === 0 ? "" : value > 0 ? "up" : "down";
