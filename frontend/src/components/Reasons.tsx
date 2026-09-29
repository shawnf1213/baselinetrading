// A disabled control must say why. This renders the risk manager's own reasons, verbatim.
export default function Reasons({ reasons, title = "Disabled because", tone = "warn" }: {
  reasons: string[];
  title?: string;
  tone?: "warn" | "bad" | "info";
}) {
  if (!reasons.length) return null;
  return (
    <div className={`callout ${tone === "warn" ? "" : tone}`} role="status">
      <div className="callout-title">
        <span className="dot" />
        {title}
      </div>
      <ul>
        {reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </div>
  );
}
