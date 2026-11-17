import { Link, useNavigate, useParams } from "react-router-dom";
import { PriceChart } from "../components/charts";
import { Card, Empty, ErrorBox, ExternalLink, ScoreBadge, Stat, WalletLink } from "../components/ui";
import { useMarket, useSettings } from "../lib/api";
import { cents, date, duration, num, polymarketMarket, usd } from "../lib/format";

export default function MarketPage() {
  const { cid = "" } = useParams();
  const { data, error } = useMarket(cid);
  const settings = useSettings();
  const nav = useNavigate();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="loading">Loading…</div>;
  const m = data.market;
  const preH = settings.data?.analysis?.pre_jump_hours ?? 12;

  return (
    <div>
      <div className="crumb"><Link to="/markets">Markets</Link> / {m.event_title && m.event_title !== m.question ? m.event_title : "market"}</div>
      <div className="page-head">
        <div>
          <h1>{m.question}</h1>
          <p className="flow">
            {m.resolved ? <span>Resolved <b>{m.outcomes[m.winning_index ?? 0]}</b>{m.closed_ts ? ` on ${date(m.closed_ts, false)}` : ""}</span> : <span>Open · “{m.outcomes[0]}” at {cents(m.last_price)}</span>}
            <ExternalLink href={polymarketMarket(m.slug)}>View on Polymarket</ExternalLink>
          </p>
        </div>
      </div>
      <div className="stats">
        <Stat label="Market volume" value={usd(m.volume)} />
        <Stat label="Trades stored" value={num(m.n_trades)} hint={usd(m.our_volume)} />
        <Stat label="Wallets" value={num(m.n_wallets)} />
        <Stat label="News moves detected" value={data.jumps.length} />
        <Stat label="Flagged trades shown" value={num(data.flagged_trades.length)} hint="score ≥ 30, or ≥ $5k" />
      </div>

      <div style={{ marginBottom: 16 }}>
        <Card title="Price and who traded when" sub="Dots are trades by flagged wallets plus every trade of $5k or more, sized by amount. Hover for details, click to open the wallet.">
          <PriceChart series={data.series} jumps={data.jumps} trades={data.flagged_trades} outcomes={m.outcomes} preHours={preH} />
        </Card>
      </div>

      {data.jumps.map((j) => (
        <div key={j.id} style={{ marginBottom: 16 }}>
          <Card
            title={<>{j.direction > 0 ? "▲" : "▼"} {cents(j.p_before)} → {cents(j.p_after)} on {date(j.start_ts)}</>}
            sub={`Who bought “${j.direction > 0 ? m.outcomes[0] : m.outcomes[1] ?? "No"}” in the ${preH} h before this move`}
            flush
          >
            <div className="table-wrap">
              <table>
                <thead><tr><th>Wallet</th><th className="r">Score</th><th className="r">Amount</th><th className="r">Avg price</th><th className="r">How early</th><th>Cluster</th></tr></thead>
                <tbody>
                  {j.buyers.map((b) => (
                    <tr key={b.wallet} className="clickable" onClick={() => nav(`/wallets/${b.wallet}`)}>
                      <td><WalletLink address={b.wallet} name={b.name} /></td>
                      <td className="r">{b.score != null ? <ScoreBadge score={b.score} /> : <span className="muted">—</span>}</td>
                      <td className="r num">{usd(b.usd)}</td>
                      <td className="r num">{cents(b.avg_price)}</td>
                      <td className="r num">{duration(b.hours_before)} before</td>
                      <td>{b.cluster_key ? <Link to={`/clusters/${b.cluster_key}`} onClick={(e) => e.stopPropagation()} className="mono small">{b.cluster_key.slice(0, 6)}</Link> : <span className="muted">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!j.buyers.length && <Empty title="Nobody bought ahead of this move" />}
            </div>
          </Card>
        </div>
      ))}

      <Card title="Biggest buyers" sub="Total bought per wallet and outcome" flush>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Wallet</th><th className="r">Score</th><th>Outcome</th><th className="r">Bought</th><th>Cluster</th></tr></thead>
            <tbody>
              {data.top_buyers.map((b) => (
                <tr key={b.wallet + b.outcome_index} className="clickable" onClick={() => nav(`/wallets/${b.wallet}`)}>
                  <td><WalletLink address={b.wallet} name={b.name} /></td>
                  <td className="r">{b.score != null ? <ScoreBadge score={b.score} /> : <span className="muted">—</span>}</td>
                  <td>{m.outcomes[b.outcome_index] ?? b.outcome_index}</td>
                  <td className="r num">{usd(b.usd)}</td>
                  <td>{b.cluster_key ? <Link to={`/clusters/${b.cluster_key}`} onClick={(e) => e.stopPropagation()} className="mono small">{b.cluster_key.slice(0, 6)}</Link> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
