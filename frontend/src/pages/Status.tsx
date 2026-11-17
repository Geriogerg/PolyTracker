import { Card, ErrorBox, Stat } from "../components/ui";
import { useRunAnalysis, useStatus } from "../lib/api";
import { ago, date, num } from "../lib/format";

const TASKS: Record<string, string> = {
  markets: "Market list sync",
  backfill: "Historical trades backfill",
  poll: "Live trades (polling)",
  websocket: "Live trades (websocket)",
  trades: "Trades stored",
  prices: "Price history",
  tracer: "On-chain tracing",
  analysis: "Analysis",
  demo: "Demo feed simulator",
};

export default function StatusPage() {
  const { data, error } = useStatus();
  const run = useRunAnalysis();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="loading">Loading…</div>;
  const c = data.counts;

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Status</h1>
          <p>What the background worker is doing. Each task reports a heartbeat; errors stay visible until the task succeeds again.</p>
        </div>
        <button className="btn primary" onClick={() => run.mutate()} disabled={run.isPending}>{run.isSuccess ? "Analysis queued ✓" : "Run analysis now"}</button>
      </div>
      <div className="stats">
        <Stat label="Markets tracked" value={num(c.tracked_markets)} hint={`${num(c.backfilled_markets)} backfilled`} />
        <Stat label="Trades" value={num(c.trades)} />
        <Stat label="Wallets" value={num(c.wallets)} hint={`${num(c.traced_wallets)} traced on-chain`} />
        <Stat label="Transfers" value={num(c.transfers)} hint={`${num(c.hubs)} exchange/bridge hubs`} />
        <Stat label="Price points" value={num(c.price_points)} />
      </div>
      <div className="grid cols-2e">
        <Card title="Worker tasks" flush>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Task</th><th>Last success</th><th>Last error</th><th>Counters</th></tr></thead>
              <tbody>
                {data.workers.sort((a, b) => a.task.localeCompare(b.task)).map((w) => {
                  const failing = w.last_error_at && (!w.last_ok || w.last_error_at > w.last_ok);
                  return (
                    <tr key={w.task}>
                      <td><span className="flow"><i className={`dot ${failing ? "err" : w.last_ok ? "ok" : "idle"}`} />{TASKS[w.task] ?? w.task}</span></td>
                      <td className="small">{ago(w.last_ok, data.time)}</td>
                      <td className="small" style={{ maxWidth: 280 }}>{w.last_error ? <span className={failing ? "bad" : "muted"} title={w.last_error}>{w.last_error.slice(0, 90)} <span className="muted">({ago(w.last_error_at, data.time)})</span></span> : <span className="muted">—</span>}</td>
                      <td className="small muted mono">{Object.entries(w.counters).slice(0, 4).map(([k, v]) => `${k.replace(/^n_/, "")} ${typeof v === "number" ? num(v) : v}`).join(" · ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Analysis runs" flush>
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Finished</th><th className="r">Took</th><th className="r">Wallets</th><th className="r">Clusters</th><th className="r">Moves</th></tr></thead>
              <tbody>
                {data.runs.map((r) => (
                  <tr key={r.id}>
                    <td className="num">{r.id}</td>
                    <td className="small">{r.status === "done" ? date(r.finished_at) : <span className={r.status === "failed" ? "bad" : "muted"} title={r.error ?? ""}>{r.status}</span>}</td>
                    <td className="r num">{r.duration_s != null ? `${r.duration_s}s` : "—"}</td>
                    <td className="r num">{num(r.wallets_scored)}</td>
                    <td className="r num">{r.clusters ?? "—"}</td>
                    <td className="r num">{r.jumps ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
