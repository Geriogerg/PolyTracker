import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorBox, ScoreBadge } from "../components/ui";
import { useAck, useAlerts } from "../lib/api";
import { ago, date } from "../lib/format";

const KIND = { cluster: "Cluster", wallet: "Wallet", trade: "Live trade" } as const;

export default function AlertsPage() {
  const { data, error } = useAlerts();
  const ack = useAck();
  const [kind, setKind] = useState<string>("all");
  const [unread, setUnread] = useState(false);
  const rows = (data ?? []).filter((a) => (kind === "all" || a.kind === kind) && (!unread || !a.acknowledged));

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p>Raised when a cluster or wallet crosses its score threshold, or when a high-scoring wallet places a large trade. Delivered to Telegram or Discord when configured.</p>
        </div>
        <button className="btn" onClick={() => ack.mutate("all")} disabled={!data?.some((a) => !a.acknowledged)}>Mark all as read</button>
      </div>
      <div className="toolbar">
        <div className="seg" role="group" aria-label="Kind">
          {["all", "cluster", "wallet", "trade"].map((k) => (
            <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{k === "all" ? "All" : KIND[k as keyof typeof KIND]}</button>
          ))}
        </div>
        <label className="flow small"><input type="checkbox" checked={unread} onChange={(e) => setUnread(e.target.checked)} /> Unread only</label>
      </div>
      {error && <ErrorBox error={error} />}
      <section className="card">
        {rows.map((a) => (
          <div key={a.id} className="cluster-card" style={{ opacity: a.acknowledged ? 0.6 : 1 }}>
            <ScoreBadge score={a.score} />
            <div style={{ minWidth: 0 }}>
              <div className="title">
                <span className="chip" style={{ marginRight: 8 }}>{KIND[a.kind]}</span>
                <Link to={a.kind === "cluster" ? `/clusters/${a.ref}` : `/wallets/${a.ref}`} style={{ color: "var(--ink)" }}>{a.title}</Link>
              </div>
              <div className="why">{a.message}</div>
              <div className="meta">
                {date(a.created_at)} ({ago(a.created_at)}){a.channels.length ? ` · sent to ${a.channels.join(", ")}` : " · not sent (no channel configured)"}
              </div>
            </div>
            {!a.acknowledged ? <button className="btn ghost" onClick={() => ack.mutate(a.id)}>Mark read</button> : <span className="small muted">read</span>}
          </div>
        ))}
        {data && !rows.length && <Empty title="No alerts">Nothing has crossed the thresholds yet.</Empty>}
      </section>
    </div>
  );
}
