import { Fragment, useState } from "react";
import type { JournalEvent } from "../types";
import { etTime } from "../format";
import { Card, Chip, type Tone } from "./ui";

const SKIP = new Set(["ts", "kind", "source"]);
const ALERTS = new Set(["veto", "error", "unprotected", "kill_switch"]);
const GOOD = new Set(["fill", "trade_opened", "risk_approved"]);
const FIRST = 12;

function summary(e: JournalEvent): string {
  return Object.entries(e)
    .filter(([k, v]) => !SKIP.has(k) && v !== null && typeof v !== "object")
    .map(([k, v]) => `${k}=${typeof v === "number" && !Number.isInteger(v) ? Number(v.toFixed(4)) : v}`)
    .concat(Array.isArray(e.reasons) ? [`reasons: ${(e.reasons as string[]).join("; ")}`] : [])
    .join("  ");
}

const kindTone = (kind: string): Tone => (ALERTS.has(kind) ? "bad" : GOOD.has(kind) ? "good" : kind === "exit" || kind === "trade_closed" ? "info" : "neutral");

// The journal: every signal, veto, order, fill and exit the backend wrote, newest first.
export default function EventLog({ events, className = "" }: { events: JournalEvent[]; className?: string }) {
  const [all, setAll] = useState(false);
  const shown = all ? events : events.slice(0, FIRST);
  return (
    <Card title="Journal" sub="every signal, veto, order, fill and exit, newest first" className={className}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Event</th>
              <th className="hide-sm">Details</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr className="empty-row">
                <td colSpan={4}>Nothing recorded yet today.</td>
              </tr>
            )}
            {shown.map((e, i) => {
              const alert = ALERTS.has(e.kind) ? " alert" : "";
              return (
                <Fragment key={`${e.ts}-${i}`}>
                  <tr className={`has-detail${alert}`}>
                    <td className="num" style={{ textAlign: "left" }}>{etTime(e.ts)}</td>
                    <td className="dim">{e.source}</td>
                    <td><Chip tone={kindTone(e.kind)}>{e.kind.replace(/_/g, " ")}</Chip></td>
                    <td className="summary hide-sm">{summary(e)}</td>
                  </tr>
                  <tr className={`detail-row${alert}`} aria-hidden>
                    <td colSpan={4} className="summary">{summary(e)}</td>
                  </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {events.length > FIRST && (
        <button className="btn link show-more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${events.length}`}
        </button>
      )}
    </Card>
  );
}
