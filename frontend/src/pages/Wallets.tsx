import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorBox, ScoreBadge, WalletLink } from "../components/ui";
import { useWallets } from "../lib/api";
import { ago, pct, usd } from "../lib/format";

export default function WalletsPage() {
  const [sort, setSort] = useState("score");
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const { data, error, isFetching } = useWallets(sort, search);
  const nav = useNavigate();
  useEffect(() => {
    const t = setTimeout(() => setSearch(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Wallets</h1>
          <p>Every wallet active in tracked markets, scored individually. The score combines wins against the odds, timing before news, wallet age at its first big bet, and on-chain ties.</p>
        </div>
      </div>
      <div className="toolbar">
        <input className="input" placeholder="Search address or name" value={input} onChange={(e) => setInput(e.target.value)} style={{ width: 280 }} aria-label="Search wallets" />
        <span className="small muted">Sort by</span>
        <div className="seg" role="group" aria-label="Sort">
          {[["score", "Score"], ["volume", "Wagered"], ["pnl", "P&L"]].map(([k, l]) => (
            <button key={k} className={sort === k ? "on" : ""} onClick={() => setSort(k)}>{l}</button>
          ))}
        </div>
      </div>
      {error && <ErrorBox error={error} />}
      <section className={`card ${isFetching ? "loading" : ""}`}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Wallet</th><th className="r">Score</th><th>Cluster</th><th className="r">Wagered</th><th className="r">Markets</th>
                <th className="r">Win rate</th><th className="r">Pre-news bets</th><th className="r">P&amp;L</th><th>Last bet</th>
              </tr>
            </thead>
            <tbody>
              {data?.map((w) => (
                <tr key={w.wallet} className="clickable" onClick={() => nav(`/wallets/${w.wallet}`)}>
                  <td><WalletLink address={w.wallet} name={w.name} /></td>
                  <td className="r"><ScoreBadge score={w.score} /></td>
                  <td>{w.cluster_key ? <Link to={`/clusters/${w.cluster_key}`} onClick={(e) => e.stopPropagation()} className="mono small">{w.cluster_key.slice(0, 6)}</Link> : <span className="muted">—</span>}</td>
                  <td className="r num">{usd(w.volume)}</td>
                  <td className="r num">{w.n_markets}</td>
                  <td className="r num">{pct(w.win_rate)} <span className="muted tiny">of {w.resolved}</span></td>
                  <td className="r num">{w.timing_hits || <span className="muted">0</span>}</td>
                  <td className={`r num ${w.pnl > 0 ? "good" : w.pnl < 0 ? "bad" : ""}`}>{usd(w.pnl, true)}</td>
                  <td className="small muted">{ago(w.last_ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && !data.length && <Empty title="No wallets match" />}
        </div>
      </section>
    </div>
  );
}
