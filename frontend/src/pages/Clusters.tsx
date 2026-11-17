import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ClusterGraph } from "../components/ClusterGraph";
import { useTheme } from "../components/Layout";
import { Empty, ErrorBox, KindKey, ScoreBadge } from "../components/ui";
import { useClusters, useGraph } from "../lib/api";
import { usd } from "../lib/format";

const KINDS = ["funding_shared", "direct_transfer", "withdrawal_shared", "co_bet"];

export default function ClustersPage() {
  const [minScore, setMinScore] = useState(0);
  const [focus, setFocus] = useState<string | null>(null);
  const [kinds, setKinds] = useState(new Set(KINDS));
  const graph = useGraph(minScore);
  const clusters = useClusters(minScore);
  const { theme } = useTheme();
  const nav = useNavigate();

  const toggle = (k: string) =>
    setKinds((s) => {
      const n = new Set(s);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Clusters</h1>
          <p>Every dot is a wallet, sized by money wagered and coloured by its score. Lines are the evidence linking two wallets. Click a cluster to focus it, a dot to open the wallet.</p>
        </div>
      </div>
      <div className="toolbar">
        <span className="small muted">Minimum cluster score</span>
        <div className="seg" role="group" aria-label="Minimum cluster score">
          {[0, 30, 50, 70].map((s) => (
            <button key={s} className={minScore === s ? "on" : ""} onClick={() => { setMinScore(s); setFocus(null); }}>
              {s === 0 ? "All" : `≥ ${s}`}
            </button>
          ))}
        </div>
        <span className="small muted" style={{ marginLeft: 12 }}>Show links</span>
        {KINDS.map((k) => (
          <label key={k} className="flow small" style={{ gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={kinds.has(k)} onChange={() => toggle(k)} />
            <KindKey kind={k} />
          </label>
        ))}
        {focus && <button className="btn ghost" onClick={() => setFocus(null)}>Show all clusters</button>}
      </div>
      {graph.error && <ErrorBox error={graph.error} />}
      <div className="grid cols-2">
        <div className="graph-wrap">
          {graph.data && graph.data.nodes.length > 0 ? (
            <ClusterGraph data={graph.data} focus={focus} kinds={kinds} theme={theme} onNode={(id) => nav(`/wallets/${id}`)} />
          ) : (
            graph.data && <Empty title="No linked wallets at this threshold" />
          )}
          <div className="graph-legend" aria-label="Legend">
            <span className="flow"><i className="legend-dot" style={{ background: "var(--crit)" }} /> Wallet score ≥ 70</span>
            <span className="flow"><i className="legend-dot" style={{ background: "var(--warn)" }} /> 40–69</span>
            <span className="flow"><i className="legend-dot" style={{ background: "var(--low)" }} /> Below 40</span>
          </div>
        </div>
        <section className="card graph-side">
          <div className="card-head">
            <h2>{clusters.data?.length ?? "…"} clusters</h2>
            <span className="sub">sorted by score</span>
          </div>
          {clusters.data?.map((c) => (
            <div key={c.key} className="cluster-card" style={{ cursor: "pointer", background: focus === c.key ? "var(--surface-2)" : undefined }}
              onClick={() => setFocus(focus === c.key ? null : c.key)} role="button" tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && setFocus(c.key)}>
              <ScoreBadge score={c.score} />
              <div style={{ minWidth: 0 }}>
                <div className="title">{c.size} wallets · {usd(c.volume)}</div>
                <div className="meta" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.top_market?.question ?? "—"}
                </div>
                <div className="meta">{c.n_structural ? `${c.n_structural} money-flow links` : "behaviour links only"}</div>
              </div>
              <Link to={`/clusters/${c.key}`} onClick={(e) => e.stopPropagation()} className="small">Open →</Link>
            </div>
          ))}
          {clusters.data && !clusters.data.length && <Empty title="No clusters at this threshold" />}
        </section>
      </div>
    </div>
  );
}
