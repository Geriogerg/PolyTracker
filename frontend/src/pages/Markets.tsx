import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Empty, ErrorBox } from "../components/ui";
import { useMarkets } from "../lib/api";
import { ago, cents, date, num, usd } from "../lib/format";

export default function MarketsPage() {
  const [status, setStatus] = useState("all");
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const { data, error, isFetching } = useMarkets(status, search);
  const nav = useNavigate();
  useEffect(() => {
    const t = setTimeout(() => setSearch(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Markets</h1>
          <p>Markets in the tracking scope (change it in Settings). Resolved markets stay tracked for a while: their outcomes are what reveal who was right.</p>
        </div>
      </div>
      <div className="toolbar">
        <input className="input" placeholder="Search questions" value={input} onChange={(e) => setInput(e.target.value)} style={{ width: 280 }} aria-label="Search markets" />
        <div className="seg" role="group" aria-label="Status">
          {[["all", "All"], ["open", "Open"], ["resolved", "Closed"]].map(([k, l]) => (
            <button key={k} className={status === k ? "on" : ""} onClick={() => setStatus(k)}>{l}</button>
          ))}
        </div>
        <span className="small muted">{data ? `${data.length} markets` : ""}</span>
      </div>
      {error && <ErrorBox error={error} />}
      <section className={`card ${isFetching ? "loading" : ""}`}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Question</th><th>Status</th><th className="r">Price</th><th className="r">Market volume</th>
                <th className="r">Trades stored</th><th className="r">Wallets</th><th className="r">News moves</th><th className="r">Flagged wallets</th><th>Last trade</th>
              </tr>
            </thead>
            <tbody>
              {data?.map((m) => (
                <tr key={m.condition_id} className="clickable" onClick={() => nav(`/markets/${m.condition_id}`)}>
                  <td>
                    <span className="q" title={m.question}>{m.question}</span>
                    <span className="tiny muted">{m.tags.slice(0, 3).join(" · ")}</span>
                  </td>
                  <td className="small">
                    {m.resolved ? <>Resolved <b>{m.outcomes[m.winning_index ?? 0]}</b></> : m.closed ? "Closed" : <>Open{m.end_ts ? <span className="muted"> · ends {date(m.end_ts, false)}</span> : null}</>}
                    {!m.backfilled && <div className="tiny muted">backfilling…</div>}
                  </td>
                  <td className="r num">{m.resolved ? "—" : cents(m.last_price)}</td>
                  <td className="r num">{usd(m.volume)}</td>
                  <td className="r num">{num(m.n_trades)}</td>
                  <td className="r num">{num(m.n_wallets)}</td>
                  <td className="r num">{m.n_jumps || <span className="muted">0</span>}</td>
                  <td className="r num">{m.n_flagged ? <b>{m.n_flagged}</b> : <span className="muted">0</span>}</td>
                  <td className="small muted">{ago(m.last_trade_ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && !data.length && <Empty title="No markets in scope">Check the scope on the Settings page, or wait for the next market sync.</Empty>}
        </div>
      </section>
    </div>
  );
}
