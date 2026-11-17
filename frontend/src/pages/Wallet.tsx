import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Sparkline } from "../components/charts";
import { Card, Empty, ErrorBox, ExternalLink, KindKey, Ledger, ScoreBadge, Stat, WalletLink } from "../components/ui";
import { useWallet } from "../lib/api";
import { ago, cents, date, duration, pct, polygonscan, polygonTx, polymarketProfile, short, usd } from "../lib/format";
import { ClusterCard } from "./Overview";

export default function WalletPage() {
  const { address = "" } = useParams();
  const { data, error } = useWallet(address.toLowerCase());
  const [allTrades, setAllTrades] = useState(false);
  const [copied, setCopied] = useState(false);
  const nav = useNavigate();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="loading">Loading…</div>;
  const w = data.wallet;
  const s = data.score;
  const st = s?.stats ?? {};
  const firstBig = st.first_funded_ts && st.max_fresh_bet ? st.max_fresh_bet : null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(w.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div>
      <div className="crumb"><Link to="/wallets">Wallets</Link> / <span className="mono">{short(w.address)}</span></div>
      <div className="page-head">
        <div className="flow" style={{ gap: 16 }}>
          <ScoreBadge score={s?.score ?? 0} size="lg" />
          <div style={{ minWidth: 0 }}>
            <h1>{w.name ?? w.pseudonym ?? short(w.address, 6)}</h1>
            <div className="flow small" style={{ marginTop: 4 }}>
              <span className="addr dim" style={{ overflowWrap: "anywhere" }}>{w.address}</span>
              <button className="btn ghost" style={{ padding: "2px 8px" }} onClick={copy}>{copied ? "Copied" : "Copy"}</button>
              <ExternalLink href={polymarketProfile(w.address)}>Polymarket</ExternalLink>
              <ExternalLink href={polygonscan(w.address)}>Polygonscan</ExternalLink>
            </div>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="small muted">Score across analysis runs</div>
          <Sparkline values={data.history.map((h) => h.score)} label="Wallet score history" />
        </div>
      </div>
      {!s && <div className="notice">This wallet is below the activity threshold, so it isn’t scored. Its trades are listed below.</div>}

      <div className="stats">
        <Stat label="Wagered" value={usd(st.volume ?? 0)} hint={`${st.n_markets ?? 0} markets, ${st.n_bets ?? 0} bets`} />
        <Stat label="Resolved bets won" value={st.resolved ? `${st.wins} of ${st.resolved}` : "—"} hint={st.resolved ? `chance-level expectation ${st.expected_wins}` : "none resolved yet"} />
        <Stat label="P&L on resolved markets" value={<span className={st.pnl > 0 ? "good" : st.pnl < 0 ? "bad" : ""}>{usd(st.pnl ?? 0, true)}</span>} hint={st.roi != null ? `${pct(st.roi)} return` : undefined} />
        <Stat label="Episodes just before news" value={st.timing_trials ? `${st.timing_hits} of ${st.timing_trials}` : "—"} hint={st.pre_jump_usd ? `${usd(st.pre_jump_usd)} positioned` : undefined} />
        <Stat label="First funded on-chain" value={w.first_funded_ts ? date(w.first_funded_ts, false) : "—"}
          hint={w.first_funded_ts ? (firstBig ? `bet ${usd(firstBig)} soon after` : ago(w.first_funded_ts)) : w.traced_at ? "no inbound transfer found" : "not traced yet"} />
      </div>

      <div className="grid cols-2e" style={{ marginBottom: 16 }}>
        <Card title={s ? `Why this wallet scores ${s.score.toFixed(0)}` : "Score"}>
          {s ? <Ledger components={s.components} total={s.score} /> : <Empty title="Not scored" />}
        </Card>
        <div className="grid" style={{ alignContent: "start" }}>
          {data.cluster && (
            <Card title="Member of a cluster" flush>
              <ClusterCard c={data.cluster} />
            </Card>
          )}
          <Card title="Linked wallets" sub="Other wallets connected to this one in the latest analysis" flush>
            <div className="table-wrap">
              <table>
                <tbody>
                  {data.links.sort((a, b) => b.weight - a.weight).map((l) => (
                    <tr key={l.wallet} className="clickable" onClick={() => nav(`/wallets/${l.wallet}`)}>
                      <td><WalletLink address={l.wallet} name={l.name} /></td>
                      <td>{l.score != null && <ScoreBadge score={l.score} />}</td>
                      <td><div className="flow" style={{ gap: 10 }}>{l.kinds.filter((k) => k !== "behavior").map((k) => <KindKey key={k} kind={k} />)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data.links.length && <Empty title="No links to other wallets" />}
            </div>
          </Card>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <Card title="Positions" flush>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Market</th><th>Mainly backed</th><th className="r">Bought</th><th className="r">Sold</th><th>Result</th><th className="r">P&amp;L</th><th>Period</th></tr></thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={p.market.condition_id} className="clickable" onClick={() => nav(`/markets/${p.market.condition_id}`)}>
                    <td><span className="q">{p.market.question}</span></td>
                    <td>{p.market.outcomes?.[p.main_outcome] ?? `#${p.main_outcome}`}</td>
                    <td className="r num">{usd(p.buy_usd)}</td>
                    <td className="r num">{p.sell_usd ? usd(p.sell_usd) : <span className="muted">—</span>}</td>
                    <td>{p.won == null ? <span className="muted">open</span> : p.won ? <span className="good">✓ won</span> : <span className="bad">✗ lost</span>}</td>
                    <td className={`r num ${(p.pnl ?? 0) > 0 ? "good" : (p.pnl ?? 0) < 0 ? "bad" : ""}`}>{p.pnl == null ? "—" : usd(p.pnl, true)}</td>
                    <td className="small muted">{date(p.first_ts, false)}{p.last_ts - p.first_ts > 86400 ? ` → ${date(p.last_ts, false)}` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="grid cols-2e">
        <Card title="Money in and out" sub="Collateral transfers on Polygon" flush>
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: "auto" }}>
            <table>
              <thead><tr><th></th><th>Counterparty</th><th className="r">Amount</th><th>Date</th></tr></thead>
              <tbody>
                {data.transfers.map((t) => (
                  <tr key={t.tx_hash + t.counterparty.address + t.direction}>
                    <td className={t.direction === "in" ? "good" : "dim"}>{t.direction === "in" ? "← in" : "→ out"}</td>
                    <td>
                      {t.counterparty.kind === "tracked" ? <WalletLink address={t.counterparty.address} /> : <span className="addr">{short(t.counterparty.address)}</span>}{" "}
                      <span className="chip">{t.counterparty.label ?? (t.counterparty.kind === "hub" ? "exchange / bridge" : t.counterparty.kind === "tracked" ? "tracked wallet" : t.counterparty.kind === "system" ? "protocol" : "outside wallet")}</span>
                    </td>
                    <td className="r num">{usd(t.amount)}</td>
                    <td className="small muted"><a href={polygonTx(t.tx_hash)} target="_blank" rel="noreferrer">{date(t.ts, false)}</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.transfers.length && <Empty title={w.traced_at ? "No collateral transfers found" : "Not traced yet"}>{!w.traced_at && "Wallets are traced on-chain once they pass the volume threshold and an API key is configured."}</Empty>}
          </div>
        </Card>
        <Card title="Trades" sub={`${data.trades.length} most recent`} flush
          foot={data.trades.length > 60 && <button className="btn ghost" onClick={() => setAllTrades(!allTrades)}>{allTrades ? "Show fewer" : `Show all ${data.trades.length}`}</button>}>
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: "auto" }}>
            <table>
              <thead><tr><th>When</th><th>Market</th><th>Trade</th><th className="r">Amount</th></tr></thead>
              <tbody>
                {(allTrades ? data.trades : data.trades.slice(0, 60)).map((t) => (
                  <tr key={t.seq}>
                    <td className="small muted" title={date(t.ts)}>{date(t.ts, false)}</td>
                    <td><Link to={`/markets/${t.condition_id}`} className="q" style={{ maxWidth: 260, color: "var(--ink)" }}>{t.question ?? short(t.condition_id)}</Link></td>
                    <td className="small">{t.side === "BUY" ? "Buy" : "Sell"} {t.outcome} @ {cents(t.price)}</td>
                    <td className="r num">{usd(t.usd)}{t.tx_hash && !t.tx_hash.endsWith("000000") ? <> <a href={polygonTx(t.tx_hash)} target="_blank" rel="noreferrer" className="tiny">↗</a></> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
      {st.first_funded_ts && st.first_trade_ts && (
        <p className="small muted" style={{ marginTop: 12 }}>
          First trade in tracked markets {duration(Math.max(0, st.first_trade_ts - st.first_funded_ts) / 3600)} after first funding.
        </p>
      )}
    </div>
  );
}
