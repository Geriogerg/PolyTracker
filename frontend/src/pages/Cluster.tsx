import { Fragment, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { BetTimeline, Sparkline } from "../components/charts";
import { Card, Empty, ErrorBox, KindKey, Ledger, ScoreBadge, Stat, WalletLink } from "../components/ui";
import { useCluster } from "../lib/api";
import { ago, date, duration, pct, polygonTx, short, usd } from "../lib/format";
import type { ClusterDetail, EdgeT, Evidence, Party } from "../lib/types";

function PartyLabel({ p }: { p: Party }) {
  if (p.kind === "member" || p.kind === "tracked") return <WalletLink address={p.address} />;
  const tag = p.kind === "hub" ? "exchange / bridge" : p.kind === "system" ? "protocol" : "outside wallet";
  return (
    <span title={p.address}>
      <span className="addr">{short(p.address)}</span> <span className="chip">{p.label ?? tag}</span>
    </span>
  );
}

export function EvidenceText({ ev, markets, a, b }: {
  ev: Evidence; markets: ClusterDetail["markets"]; a: string; b: string;
}) {
  switch (ev.kind) {
    case "funding_shared": {
      const hops = (ev.hops as number[]) ?? [1, 1];
      return (
        <>
          Both funded by <span className="addr">{short(String(ev.via))}</span>
          {Math.max(...hops) > 1 ? " (via an intermediary)" : ""}
          {ev.funded_gap_h != null && <span className="muted">, {duration(Number(ev.funded_gap_h))} apart</span>}
        </>
      );
    }
    case "direct_transfer":
      return (
        <>
          <span className="addr">{short(String(ev.from))}</span> sent <b>{usd(Number(ev.amount))}</b> to{" "}
          <span className="addr">{short(String(ev.to))}</span> on {date(Number(ev.ts), false)}
          {ev.tx ? <> · <a href={polygonTx(String(ev.tx))} target="_blank" rel="noreferrer">tx ↗</a></> : null}
        </>
      );
    case "withdrawal_shared":
      return <>Both cashed out to <span className="addr">{short(String(ev.via))}</span></>;
    case "co_bet": {
      const ms = (ev.markets as { condition_id: string; gap_min: number; crowd: number; ts: number }[]) ?? [];
      return (
        <>
          Bet the same way on {String(ev.n_markets)} markets within minutes of each other:{" "}
          {ms.slice(0, 3).map((m, i) => (
            <Fragment key={m.condition_id}>
              {i > 0 && "; "}
              <Link to={`/markets/${m.condition_id}`}>{markets[m.condition_id]?.question ?? short(m.condition_id)}</Link>
              <span className="muted"> ({m.gap_min} min apart)</span>
            </Fragment>
          ))}
        </>
      );
    }
    case "behavior":
      return <>Very similar betting habits (similarity {Number(ev.similarity).toFixed(2)})</>;
    default:
      return <>{ev.kind} {a.slice(0, 6)}–{b.slice(0, 6)}</>;
  }
}

export default function ClusterPage() {
  const { key = "" } = useParams();
  const { data, error } = useCluster(key);
  const nav = useNavigate();

  const evidence = useMemo(() => {
    const by: Record<string, { e: EdgeT; ev: Evidence }[]> = {};
    data?.edges.forEach((e) => e.evidence.forEach((ev) => (by[ev.kind] ??= []).push({ e, ev })));
    return by;
  }, [data]);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="loading">Loading…</div>;
  const c = data.cluster;
  const st = c.stats;
  const order = ["direct_transfer", "funding_shared", "withdrawal_shared", "co_bet", "behavior"];

  return (
    <div>
      <div className="crumb"><Link to="/clusters">Clusters</Link> / <span className="mono">{c.key}</span></div>
      <div className="page-head">
        <div className="flow" style={{ gap: 16, alignItems: "center" }}>
          <ScoreBadge score={c.score} size="lg" />
          <div>
            <h1>Cluster of {c.size} wallets</h1>
            <p>
              Active {date(st.first_ts, false)} → {date(st.last_ts, false)} · {usd(st.volume)} wagered ·{" "}
              <span className={st.pnl > 0 ? "good" : "bad"}>{usd(st.pnl, true)} P&amp;L on resolved markets</span>
            </p>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="small muted">Score across analysis runs</div>
          <Sparkline values={data.history.map((h) => h.score)} label="Cluster score history" />
        </div>
      </div>
      {c.stale && <div className="notice">This cluster no longer appears in the latest analysis run; showing its last known state.</div>}

      <div className="stats">
        <Stat label="Resolved markets, right side" value={`${st.group_wins ?? 0} of ${st.resolved_markets ?? 0}`} hint={`chance-level expectation ${st.expected_wins ?? 0}`} />
        <Stat label="Episodes just before news" value={`${st.timing_hits ?? 0} of ${st.timing_trials ?? 0}`} />
        <Stat label="Money-flow links" value={`${st.n_structural ?? 0} of ${st.n_edges ?? 0}`} />
        <Stat label="Freshly funded wallets" value={`${st.fresh_members ?? 0} of ${c.size}`} />
      </div>

      <div className="grid cols-2e" style={{ marginBottom: 16 }}>
        <Card title={`Why this cluster scores ${c.score.toFixed(0)}`} sub="Each line adds points; the tests treat the group as a single trader so one tip isn’t counted nine times">
          <Ledger components={c.components} total={c.score} />
        </Card>
        <Card title="Members" flush>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Wallet</th><th className="r">Score</th><th className="r">Wagered</th><th className="r">Win rate</th><th className="r">Pre-news</th><th className="r">P&amp;L</th></tr></thead>
              <tbody>
                {data.members.map((m) => (
                  <tr key={m.wallet} className="clickable" onClick={() => nav(`/wallets/${m.wallet}`)}>
                    <td><WalletLink address={m.wallet} name={m.name} /></td>
                    <td className="r"><ScoreBadge score={m.score} /></td>
                    <td className="r num">{usd(m.volume)}</td>
                    <td className="r num">{pct(m.win_rate)}</td>
                    <td className="r num">{m.timing_hits ?? 0}</td>
                    <td className={`r num ${(m.pnl ?? 0) > 0 ? "good" : ""}`}>{usd(m.pnl, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div style={{ marginBottom: 16 }}>
        <Card title="Bets over time" sub="Each dot is one member’s buy. Red lines mark detected news moves; watch for dots stacking up just before them.">
          <BetTimeline trades={data.trades} markets={data.markets} />
        </Card>
      </div>

      <div className="grid cols-2e" style={{ marginBottom: 16 }}>
        <Card title="Evidence linking them" sub={`${data.edges.length} links between members`} flush>
          {order.filter((k) => evidence[k]?.length).map((k) => (
            <div key={k} style={{ borderBottom: "1px solid var(--line)" }}>
              <div className="card-body" style={{ paddingBottom: 6 }}>
                <div className="flow" style={{ justifyContent: "space-between" }}>
                  <KindKey kind={k} />
                  <span className="small muted">{evidence[k].length} pairs</span>
                </div>
              </div>
              <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
                <table>
                  <tbody>
                    {evidence[k].slice(0, 40).map(({ e, ev }, i) => (
                      <tr key={i}>
                        <td style={{ whiteSpace: "nowrap" }}><WalletLink address={e.a} chars={3} /> ↔ <WalletLink address={e.b} chars={3} /></td>
                        <td className="small"><EvidenceText ev={ev} markets={data.markets} a={e.a} b={e.b} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
          {!data.edges.length && <Empty title="No links stored" />}
        </Card>
        <Card title="Money flows" sub="Collateral transfers in and out of members" flush>
          <div className="table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
            <table>
              <thead><tr><th>From</th><th>To</th><th className="r">Amount</th><th>Date</th></tr></thead>
              <tbody>
                {data.transfers.map((t) => (
                  <tr key={t.tx_hash + t.from.address + t.to.address}>
                    <td><PartyLabel p={t.from} /></td>
                    <td><PartyLabel p={t.to} /></td>
                    <td className="r num">{usd(t.amount)}</td>
                    <td className="small muted">{date(t.ts, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.transfers.length && <Empty title="No on-chain transfers traced">Add an Etherscan API key to trace funding.</Empty>}
          </div>
        </Card>
      </div>

      <Card title="Markets they played" flush>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Market</th><th className="r">Members</th><th>Backed</th><th className="r">Staked</th><th>First bet</th></tr></thead>
            <tbody>
              {c.top_markets.map((m) => (
                <tr key={m.condition_id} className="clickable" onClick={() => nav(`/markets/${m.condition_id}`)}>
                  <td><span className="q">{m.question}</span></td>
                  <td className="r num">{m.n_members}</td>
                  <td>{m.backed}</td>
                  <td className="r num">{usd(m.usd)}</td>
                  <td className="small muted">{date(m.first_ts, false)} <span className="tiny">({ago(m.first_ts)})</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
