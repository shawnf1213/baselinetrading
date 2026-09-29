import { Component, type ReactNode } from "react";
import Reasons from "./Reasons";

export type Tone = "good" | "bad" | "warn" | "info" | "neutral";

// One card that fails to render must not take the rest down with it: the kill switch has to stay usable.
export class Guard extends Component<{ name: string; className?: string; children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className={`card ${this.props.className ?? ""}`}>
        <Reasons tone="bad" title={`${this.props.name} could not be shown`} reasons={[this.state.error.message]} />
      </section>
    );
  }
}

// A state pill: a dot plus a label, so state never rides on color alone.
export function Pill({ tone = "neutral", pulse = false, title, children }: { tone?: Tone; pulse?: boolean; title?: string; children: ReactNode }) {
  return (
    <span className={`pill ${tone}`} title={title}>
      <span className={`dot${pulse ? " pulse" : ""}`} />
      {children}
    </span>
  );
}

export function Chip({ tone = "neutral", title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <span className={`chip ${tone}`} title={title}>
      <span className="dot" />
      {children}
    </span>
  );
}

export function Card({ title, sub, right, className = "", children }: {
  title?: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="card-head">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Brand() {
  return (
    <div className="brand" aria-label="Baseline Trading">
      <span className="brand-word">BASELINE</span>
      <span className="brand-sub">TRADING</span>
    </div>
  );
}
