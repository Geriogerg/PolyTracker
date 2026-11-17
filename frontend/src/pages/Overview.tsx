import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ScoreHistogram } from "../components/charts";
import { Card, Empty, ErrorBox, ScoreBadge, Stat, WalletLink } from "../components/ui";
import { useClusters, useFeed, useOverview, useRunAnalysis } from "../lib/api";
import { ago, cents, date, num, pct, usd } from "../lib/format";
import { useLive } from "../lib/live";
import type { ClusterSummary, TradeT } from "../lib/types";

export function useNow(every = 15000) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), every);
    return () => clearInterval(t);
  }, [every]);
  return now;
}

export function ClusterCard({ c }: { c: ClusterSummary }) {
  const top = [...c.components].sort((a, b) => b.points - a.points).filter((x) => x.points > 0).slice(0, 3);
  return (
    <Link to={`/clusters/${c.key}`} className="cluster-card">
      <ScoreBadge score={c.score} size="lg" />
      <div style={{ minWidth: 0 }}>
        <div className="title">
          {c.size} wallets · {usd(c.volume)} wagered
        </div>
        <div className="meta">
          {c.top_market ? (
            <>
              Mostly “{c.top_market.question}” ({c.top_market.n_members} of {c.size} backed {c.top_market.backed})
            </>
          ) : (
            "—"
          )}
        </div>
        <div className="chips">
          {top.map((x) => (
            <span className="chip" key={x.key}>
              {x.label} +{x.points.toFixed(0)}
            </span>
          ))}
          {!top.length && <span className="chip">No suspicious signal</span>}
        </div>
      </div>
      <div className="small muted" style={{ textAlign: "right", whiteSpace: "nowrap" }}>
        {c.pnl ? <div className={c.pnl > 0 ? "good" : "bad"}>{usd(c.pnl, true)} P&amp;L</div> : null}
        <div>active {ago(c.last_ts)}</div>
      </div>
    </Link>
  );
}

export function FeedRow({ t, now }: { t: TradeT; now: number }) {
  const hot = (t.score ?? 0) >= 70;
  return (
    <div className={`feed-row ${hot ? "hot" : ""}`}>
      <span className="tiny muted num">{ago(t.ts, now).replace(" ago", "").replace("just now", "now")}</span>
      <div style={{ minWidth: 0 }}>
        <div className="what">
          <b className="num">{usd(t.usd)}</b>{" "}
          <span className="dim">{t.side === "BUY" ? "on" : "sold"} “{t.outcome}” at {cents(t.price)} ·</span>{" "}
          <Link to={`/markets/${t.condition_id}`} style={{ color: "var(--ink)" }}>{t.question ?? t.condition_id.slice(0, 10)}</Link>
        </div>
        <div className="who">
          <WalletLink address={t.wallet} name={t.name} />
          {t.cluster_key && (
            <>
              {" · "}
              <Link to={`/clusters/${t.cluster_key}`}>in a cluster</Link>
            </>
          )}
        </div>
      </div>
      {t.score != null ? <ScoreBadge score={t.score} /> : <span className="muted tiny">new</span>}
    </div>
  );
}

export function LiveFeed() {
  const [flagged, setFlagged] = useState(false);
  const { data, isLoading } = useFeed(flagged);
  const { trades: live } = useLive();
  const now = useNow(5000);
  const rows = useMemo(() => {
    const seen = new Set<number>();
    return [...live.filter((t) => !flagged || (t.score ?? 0) >= 30), ...(data ?? [])]
      .filter((t) => (seen.has(t.seq) ? false : (seen.add(t.seq), true)))
      .sort((a, b) => b.ts - a.ts || b.seq - a.seq)
      .slice(0, 60);
  }, [live, data, flagged]);
  return (
    <Card
      title="Live trades"
      sub="In tracked markets, newest first"
      flush
      actions={
        <div className="seg" role="group" aria-label="Feed filter">
          <button className={!flagged ? "on" : ""} onClick={() => setFlagged(false)}>All</button>
          <button className={flagged ? "on" : ""} onClick={() => setFlagged(true)}>Score ≥ 30</button>
        </div>
      }
    >
      <div className="feed">
        {rows.map((t) => <FeedRow key={t.seq} t={t} now={now} />)}
        {!rows.length && !isLoading && <Empty title="No trades yet">The worker is still backfilling tracked markets.</Empty>}
      </div>
    </Card>
  );
}

export default function OverviewPage() {
  const { data, error, isLoading } = useOverview();
  const flagged = useClusters(70);
  const run = useRunAnalysis();
  const nav = useNavigate();
  const now = useNow();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="loading">Loading…</div>;
  const r = data.run;

  return (
    <div className={isLoading ? "loading" : ""}>
      <div className="page-head">
        <div>
          <div className="eyebrow">Polymarket · on-chain forensics</div>
          <h1>Overview</h1>
          <p>Wallets that bet together and ahead of price-moving news, ranked by how unlikely their record is by chance.</p>
        </div>
        <div className="flow">
          <span className="small muted">
            {r ? <>Analysed {ago(r.finished_at, now)} · {num(r.wallets_scored)} wallets in {r.duration_s}s</> : "No analysis yet"}
          </span>
          <button className="btn" onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isSuccess ? "Queued ✓" : "Run analysis now"}
          </button>
        </div>
      </div>

      {data.demo && (
        <div className="notice">
          You are looking at <b>synthetic demo data</b>: a planted insider ring, a coordinated trio, copy-traders, a sybil farm
          and 300 ordinary traders. Set <code>DEMO_MODE=false</code> in <code>.env</code> to track Polymarket live.
        </div>
      )}

      <div className="stats">
        <Stat label="Tracked markets" value={num(data.totals.markets)} />
        <Stat label="Trades stored" value={num(data.totals.trades)} hint={`${num(data.totals.trades_24h)} in last 24 h`} />
        <Stat label="Wallets seen" value={num(data.totals.wallets)} />
        <Stat label="Clusters scoring ≥ 70" value={flagged.data ? flagged.data.length : "—"} hint={r ? `${r.clusters} clusters in total` : undefined} />
        <Stat label="Volume, last 24 h" value={usd(data.totals.volume_24h)} />
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <Card title="Flagged clusters" sub="Groups of linked wallets, by suspicion score" flush
          foot={<Link to="/clusters">All clusters and the link graph →</Link>}>
          <div className="cluster-list">
            {data.top_clusters.map((c) => <ClusterCard c={c} key={c.key} />)}
            {!data.top_clusters.length && <Empty title="No clusters yet">They appear after the first analysis run.</Empty>}
          </div>
        </Card>
        <LiveFeed />
      </div>

      <div className="grid cols-2e" style={{ marginBottom: 16 }}>
        <Card title="News moves and who was early" sub="Sharp price moves, with the money placed in the 12 h before" flush>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Market</th><th>Move</th><th>When</th><th className="r">Bought before</th><th className="r">Flagged</th></tr>
              </thead>
              <tbody>
                {data.jumps.map((j) => (
                  <tr key={j.id} className="clickable" onClick={() => nav(`/markets/${j.condition_id}`)}>
                    <td><span className="q" title={j.question}>{j.question}</span></td>
                    <td className="num">{cents(j.p_before)} → {cents(j.p_after)}</td>
                    <td className="small muted">{date(j.start_ts, false)}</td>
                    <td className="r num">{usd(j.usd_before)}</td>
                    <td className="r num">{j.n_flagged ? <b>{j.n_flagged}</b> : <span className="muted">0</span>} / {j.n_buyers}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.jumps.length && <Empty title="No sharp moves detected yet" />}
          </div>
        </Card>
        <Card title="Highest-scoring wallets" flush foot={<Link to="/wallets">All wallets →</Link>}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Wallet</th><th className="r">Score</th><th className="r">Wagered</th><th className="r">Win rate</th><th className="r">P&amp;L</th></tr></thead>
              <tbody>
                {data.top_wallets.map((w) => (
                  <tr key={w.wallet} className="clickable" onClick={() => nav(`/wallets/${w.wallet}`)}>
                    <td><WalletLink address={w.wallet} name={w.name} /></td>
                    <td className="r"><ScoreBadge score={w.score} /></td>
                    <td className="r num">{usd(w.volume)}</td>
                    <td className="r num">{pct(w.win_rate)} <span className="muted tiny">of {w.resolved}</span></td>
                    <td className={`r num ${w.pnl > 0 ? "good" : w.pnl < 0 ? "bad" : ""}`}>{usd(w.pnl, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="grid cols-2e">
        <Card title="Recent alerts" flush foot={<Link to="/alerts">All alerts →</Link>}>
          {data.alerts.map((a) => (
            <Link key={a.id} to={a.kind === "cluster" ? `/clusters/${a.ref}` : `/wallets/${a.ref}`} className="cluster-card" style={{ gridTemplateColumns: "auto minmax(0,1fr) auto" }}>
              <ScoreBadge score={a.score} />
              <div style={{ minWidth: 0 }}>
                <div className="title">{a.title}</div>
                <div className="why">{a.message}</div>
              </div>
              <span className="tiny muted">{ago(a.created_at, now)}</span>
            </Link>
          ))}
          {!data.alerts.length && <Empty title="No alerts">Thresholds are set on the Settings page.</Empty>}
        </Card>
        <Card title="Score distribution" sub="Wallets per score band, log scale. Most wallets should sit near zero.">
          {data.score_histogram.length ? <ScoreHistogram bins={data.score_histogram} /> : <Empty title="No scores yet" />}
          <table style={{ marginTop: 10 }}>
            <caption className="sr-only">Wallets per score band</caption>
            <tbody>
              <tr>
                <td className="small muted">Below 40</td>
                <td className="r num">{num(data.score_histogram.slice(0, 4).reduce((a, b) => a + b, 0))}</td>
                <td className="small muted">40–69</td>
                <td className="r num">{num(data.score_histogram.slice(4, 7).reduce((a, b) => a + b, 0))}</td>
                <td className="small muted">70 and up</td>
                <td className="r num">{num(data.score_histogram.slice(7).reduce((a, b) => a + b, 0))}</td>
              </tr>
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
