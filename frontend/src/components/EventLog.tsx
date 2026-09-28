import type { JournalEvent } from "../types";
import { etTime } from "../format";

const SKIP = new Set(["ts", "kind", "source"]);

function summary(e: JournalEvent): string {
  return Object.entries(e)
    .filter(([k, v]) => !SKIP.has(k) && v !== null && typeof v !== "object")
    .map(([k, v]) => `${k}=${typeof v === "number" && !Number.isInteger(v) ? Number(v.toFixed(4)) : v}`)
    .concat(Array.isArray(e.reasons) ? [`reasons: ${(e.reasons as string[]).join("; ")}`] : [])
    .join("  ");
}

export default function EventLog({ events }: { events: JournalEvent[] }) {
  return (
    <div>
      <div className="panel-title">Journal (newest first)</div>
      <table className="log">
        <tbody>
          {events.map((e, n) => (
            <tr key={`${e.ts}-${n}`} className={["veto", "error", "unprotected", "kill_switch"].includes(e.kind) ? "alert" : ""}>
              <td>{etTime(e.ts)}</td>
              <td><span className={`tag ${e.source}`}>{e.source}</span></td>
              <td>{e.kind}</td>
              <td className="reason">{summary(e)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
