// A disabled control must say why. This renders the risk manager's own reasons.
export default function Reasons({ reasons, title = "Disabled because" }: { reasons: string[]; title?: string }) {
  if (!reasons.length) return null;
  return (
    <div className="reasons" role="status">
      <strong>{title}:</strong>
      <ul>
        {reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </div>
  );
}
