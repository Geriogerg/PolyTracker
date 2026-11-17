import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { KIND_LABEL, severity, severityLabel, short } from "../lib/format";
import type { Component } from "../lib/types";

export function ScoreBadge({ score, size }: { score: number | null | undefined; size?: "lg" }) {
  const shown = Math.round(score ?? 0);
  const sev = severity(shown);
  return (
    <span className={`score ${sev} ${size ?? ""}`} title={`${severityLabel[sev]} suspicion: ${(score ?? 0).toFixed(1)}/100`}>
      {shown}
      <span className="sr-only"> out of 100, {severityLabel[sev]} suspicion</span>
    </span>
  );
}

export function WalletLink({ address, name, chars = 4 }: { address: string; name?: string | null; chars?: number }) {
  return (
    <Link to={`/wallets/${address}`} className="addr" title={address}>
      {name ? (
        <>
          <span style={{ fontFamily: "var(--sans)" }}>{name}</span>{" "}
          <span className="muted small">{short(address, 3)}</span>
        </>
      ) : (
        short(address, chars)
      )}
    </Link>
  );
}

export function Ledger({ components, total, compact }: { components: Component[]; total?: number; compact?: boolean }) {
  const rows = compact ? components.filter((c) => c.points > 0) : components;
  return (
    <div className="ledger">
      {rows.map((c) => (
        <div className="ledger-row" key={c.key}>
          <div className={`ledger-pts ${c.points ? "" : "zero"}`}>
            {c.points ? `+${c.points.toFixed(1)}` : "0"}
            <small>of {c.max}</small>
          </div>
          <div>
            <div className="ledger-label">{c.label}</div>
            {c.detail && <div className="ledger-detail">{c.detail}</div>}
            <div className="meter" aria-hidden>
              <i style={{ width: `${(100 * c.points) / c.max}%` }} />
            </div>
          </div>
        </div>
      ))}
      {total !== undefined && (
        <div className="ledger-total">
          <ScoreBadge score={total} />
          <span className="dim">total, capped at 100</span>
        </div>
      )}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Card({
  title, sub, actions, children, foot, flush,
}: { title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; foot?: ReactNode; flush?: boolean }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {sub && <div className="sub">{sub}</div>}
          </div>
          {actions}
        </div>
      )}
      {flush ? children : <div className="card-body">{children}</div>}
      {foot && <div className="card-foot">{foot}</div>}
    </section>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <div>{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="error-box">Couldn’t load this: {msg}</div>;
}

export function KindKey({ kind }: { kind: string }) {
  const color: Record<string, string> = {
    funding_shared: "var(--edge-funding)",
    direct_transfer: "var(--edge-transfer)",
    withdrawal_shared: "var(--edge-withdrawal)",
    co_bet: "var(--edge-cobet)",
    behavior: "var(--edge-cobet)",
  };
  return (
    <span className="kind-key">
      <i className={kind === "co_bet" || kind === "behavior" ? "dash" : ""} style={{ borderColor: color[kind] }} />
      {KIND_LABEL[kind] ?? kind}
    </span>
  );
}

export function ExternalLink({ href, children }: { href: string | null; children: ReactNode }) {
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children} ↗
    </a>
  );
}
