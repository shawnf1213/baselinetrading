import { Fragment, useState } from "react";
import type { JournalEvent } from "../types";
import { actionLabel, actionTone, etHm, num, pct, price } from "../format";
import { Card, Chip } from "./ui";

type Inputs = Record<string, number | string | null>;
const n = (v: unknown) => (typeof v === "number" ? v : undefined);
const FIRST = 8;

// Every strategy decision with the inputs behind it, not just buy / no trade.
// Breakout decisions (with a symbol) and 15:30 decisions have different inputs.
export default function SignalsTable({ signals, options, className = "" }: { signals: JournalEvent[]; options: boolean; className?: string }) {
  const [all, setAll] = useState(false);
  const breakout = signals.some((s) => s.symbol) || signals.length === 0;
  const shown = all ? signals : signals.slice(0, FIRST);
  return (
    <Card title="Strategy decisions" sub="newest first, with the numbers behind each one" className={className}>
      <div className="table-wrap">
        <table>
          <thead>
            {breakout ? (
              <tr>
                <th>Time</th>
                <th>Stock</th>
                <th>Decision</th>
                <th className="hide-sm">Why</th>
                <th className="num hide-sm">Range</th>
                <th className="num hide-sm">High</th>
                <th className="num hide-sm">Low</th>
                <th className="num hide-sm">Crossed at</th>
              </tr>
            ) : (
              <tr>
                <th>Date</th>
                <th>Decision</th>
                <th className="hide-sm">Why</th>
                <th className="num">Prev close</th>
                <th className="num">10:00 price</th>
                <th className="num">Morning return</th>
                <th className="num">15:29 price</th>
                <th className="num hide-sm">15:00–15:30 range</th>
                <th className="num hide-sm">Volume (signal / entry)</th>
              </tr>
            )}
          </thead>
          <tbody>
            {signals.length === 0 && (
              <tr className="empty-row">
                <td colSpan={9}>No decisions yet today. Each stock is checked every minute after its opening range closes.</td>
              </tr>
            )}
            {shown.map((s) => {
              const i = (s.inputs ?? null) as Inputs | null;
              const action = String(s.action);
              const why = (
                <tr className="detail-row" aria-hidden>
                  <td colSpan={9} className="dim small">{String(s.reason)}</td>
                </tr>
              );
              if (breakout) {
                return (
                  <Fragment key={`${s.ts}-${String(s.symbol ?? "")}`}>
                    <tr className="has-detail">
                      <td className="num" style={{ textAlign: "left" }}>{etHm(s.ts)}</td>
                      <td className="sym">{String(s.symbol ?? "—")}</td>
                      <td>
                        <Chip tone={actionTone(action)}>{actionLabel(action, options)}</Chip>
                        {typeof s.entry_number === "number" && <span className="sub">entry {s.entry_number} of the day</span>}
                      </td>
                      <td className="wrap dim small hide-sm">{String(s.reason)}</td>
                      <td className="num hide-sm dim">{s.range_minutes ? `${String(s.range_minutes)} min` : "—"}</td>
                      <td className="num hide-sm">{price(n(i?.range_high))}</td>
                      <td className="num hide-sm">{price(n(i?.range_low))}</td>
                      <td className="num hide-sm">{i?.breakout_time ? `${String(i.breakout_time)} · ${price(n(i.breakout_close))}` : "—"}</td>
                    </tr>
                    {why}
                  </Fragment>
                );
              }
              return (
                <Fragment key={s.ts}>
                  <tr className="has-detail">
                    <td className="num" style={{ textAlign: "left" }}>{String(s.date ?? s.ts.slice(0, 10))}</td>
                    <td><Chip tone={actionTone(action)}>{actionLabel(action, options)}</Chip></td>
                    <td className="wrap dim small hide-sm">{String(s.reason)}</td>
                    <td className="num">{num(n(i?.previous_close))}</td>
                    <td className="num">{num(n(i?.signal_price))}</td>
                    <td className="num">{i ? pct(n(i.signal_return) ?? NaN, 3) : "—"}</td>
                    <td className="num">{num(n(i?.reference_price))}</td>
                    <td className="num hide-sm">{i ? `${num(n(i.entry_window_range_pct), 3)}%` : "—"}</td>
                    <td className="num hide-sm">
                      {i ? `${Math.round(n(i.signal_window_volume) ?? 0).toLocaleString()} / ${Math.round(n(i.entry_window_volume) ?? 0).toLocaleString()}` : "—"}
                    </td>
                  </tr>
                  {why}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {signals.length > FIRST && (
        <button className="btn link show-more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${signals.length}`}
        </button>
      )}
    </Card>
  );
}
