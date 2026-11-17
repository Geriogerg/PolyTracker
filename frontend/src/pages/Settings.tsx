import { KeyboardEvent, useEffect, useState } from "react";
import { Card, ErrorBox } from "../components/ui";
import { useResetSettings, useSaveSettings, useSettings, useTestAlert } from "../lib/api";
import type { AlertSettings, Scope } from "../lib/types";

function TagInput({ value, onChange, placeholder, id }: { value: string[]; onChange: (v: string[]) => void; placeholder: string; id: string }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const parts = draft.split(",").map((s) => s.trim()).filter(Boolean);
    if (parts.length) onChange([...value, ...parts.filter((p) => !value.includes(p))]);
    setDraft("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add();
    } else if (e.key === "Backspace" && !draft && value.length) onChange(value.slice(0, -1));
  };
  return (
    <div className="tag-input">
      {value.map((v) => (
        <span className="chip" key={v}>
          {v}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(value.filter((x) => x !== v))}>×</button>
        </span>
      ))}
      <input id={id} value={draft} placeholder={value.length ? "" : placeholder} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} onBlur={add} />
    </div>
  );
}

function Num({ id, label, help, value, onChange, step = 1 }: { id: string; label: string; help?: string; value: number; onChange: (n: number) => void; step?: number }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" type="number" step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      {help && <span className="help">{help}</span>}
    </div>
  );
}

export default function SettingsPage() {
  const { data, error } = useSettings();
  const save = useSaveSettings();
  const reset = useResetSettings();
  const test = useTestAlert();
  const [scope, setScope] = useState<Scope | null>(null);
  const [alerts, setAlerts] = useState<AlertSettings | null>(null);

  useEffect(() => {
    if (data) {
      setScope(data.scope);
      setAlerts(data.alerts);
    }
  }, [data]);

  if (error) return <ErrorBox error={error} />;
  if (!data || !scope || !alerts) return <div className="loading">Loading…</div>;
  const set = <K extends keyof Scope>(k: K, v: Scope[K]) => setScope({ ...scope, [k]: v });
  const setA = <K extends keyof AlertSettings>(k: K, v: AlertSettings[K]) => setAlerts({ ...alerts, [k]: v });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Which markets to track and when to alert. Changes apply within a minute; the analysis tuning lives in <code>config.yaml</code>.</p>
        </div>
        <div className="flow">
          {data.overridden.length > 0 && <button className="btn ghost" onClick={() => reset.mutate()}>Reset to config.yaml</button>}
          <button className="btn primary" onClick={() => save.mutate({ scope, alerts })} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save settings"}
          </button>
        </div>
      </div>
      {save.isSuccess && <div className="notice">Saved. The market list will resync within a minute; new markets are backfilled in the background.</div>}
      {save.error && <ErrorBox error={save.error} />}
      {data.demo && <div className="notice">Demo mode: scope changes are stored but no live markets are fetched until <code>DEMO_MODE=false</code>.</div>}

      <div className="grid" style={{ gap: 16 }}>
        <Card title="Market scope" sub="What the worker watches on Polymarket">
          <div className="field" style={{ marginBottom: 18 }}>
            <label>Select markets by</label>
            <div className="seg" role="radiogroup" aria-label="Scope mode">
              {([["tags", "Tags"], ["all", "Biggest markets"], ["explicit", "Only my list"]] as const).map(([k, l]) => (
                <button key={k} role="radio" aria-checked={scope.mode === k} className={scope.mode === k ? "on" : ""} onClick={() => set("mode", k)}>{l}</button>
              ))}
            </div>
            <span className="help">
              {scope.mode === "tags" && "Markets carrying any of the Polymarket tags below."}
              {scope.mode === "all" && "The highest-volume markets across Polymarket, up to the maximum below. Heavier on the API."}
              {scope.mode === "explicit" && "Only the markets listed under ‘Always track’."}
            </span>
          </div>
          <div className="form-grid">
            {scope.mode === "tags" && (
              <div className="field">
                <label htmlFor="tags">Tags</label>
                <TagInput id="tags" value={scope.tags} onChange={(v) => set("tags", v)} placeholder="geopolitics, politics…" />
                <span className="help">Polymarket tag slugs, e.g. geopolitics, politics, middle-east, crypto.</span>
              </div>
            )}
            <div className="field">
              <label htmlFor="explicit">Always track</label>
              <TagInput id="explicit" value={scope.explicit_markets} onChange={(v) => set("explicit_markets", v)} placeholder="market slug or 0x condition id" />
              <span className="help">Tracked in every mode.</span>
            </div>
            <div className="field">
              <label htmlFor="inc">Question must contain</label>
              <TagInput id="inc" value={scope.include_keywords} onChange={(v) => set("include_keywords", v)} placeholder="iran, strike… (optional)" />
            </div>
            <div className="field">
              <label htmlFor="exc">Skip questions containing</label>
              <TagInput id="exc" value={scope.exclude_keywords} onChange={(v) => set("exclude_keywords", v)} placeholder="nfl, up or down…" />
            </div>
          </div>
          <div className="hr" />
          <div className="form-grid">
            <Num id="minvol" label="Minimum market volume ($)" value={scope.min_volume} onChange={(n) => set("min_volume", n)} step={1000} />
            <Num id="maxm" label="Maximum markets" value={scope.max_markets} onChange={(n) => set("max_markets", n)} help="Each market is backfilled and polled; more markets means more API calls." />
            <Num id="closed" label="Keep resolved markets for (days)" value={scope.include_closed_days} onChange={(n) => set("include_closed_days", n)} help="Resolved markets are what reveal win rates." />
          </div>
        </Card>

        <Card title="Alerts" sub="When to notify"
          actions={
            <button className="btn" onClick={() => test.mutate()} disabled={test.isPending || !(data.integrations.telegram || data.integrations.discord)}>
              {test.isSuccess ? "Sent ✓" : "Send test notification"}
            </button>
          }>
          <label className="flow" style={{ marginBottom: 16 }}>
            <input type="checkbox" checked={alerts.enabled} onChange={(e) => setA("enabled", e.target.checked)} /> Alerts enabled
          </label>
          <div className="form-grid">
            <Num id="cs" label="Cluster score threshold" value={alerts.cluster_score} onChange={(n) => setA("cluster_score", n)} />
            <Num id="ws" label="Wallet score threshold" value={alerts.wallet_score} onChange={(n) => setA("wallet_score", n)} />
            <Num id="lt" label="Live trade alert from ($)" value={alerts.large_trade_usd} onChange={(n) => setA("large_trade_usd", n)} step={1000} help="When a wallet above the wallet threshold trades at least this much." />
            <Num id="cd" label="Re-alert cooldown (hours)" value={alerts.cooldown_hours} onChange={(n) => setA("cooldown_hours", n)} help="Unless the score rises by 10+ points." />
          </div>
          {test.error && <div style={{ marginTop: 12 }}><ErrorBox error={test.error} /></div>}
        </Card>

        <div className="grid cols-2e">
          <Card title="Connections" sub="Set in .env, restart to apply">
            <dl className="kv">
              <dt>On-chain tracing</dt>
              <dd>{data.integrations.onchain ? <span className="good">connected ({data.onchain.provider})</span> : <span className="bad">no API key: funding links and wallet age are unavailable</span>}</dd>
              <dt>Telegram</dt>
              <dd>{data.integrations.telegram ? <span className="good">configured</span> : <span className="muted">not configured</span>}</dd>
              <dt>Discord</dt>
              <dd>{data.integrations.discord ? <span className="good">configured</span> : <span className="muted">not configured</span>}</dd>
            </dl>
          </Card>
          <Card title="Analysis parameters" sub="Read-only here; edit config.yaml">
            <dl className="kv small">
              <dt>Run every</dt><dd>{data.analysis.interval_minutes} min over the last {data.analysis.lookback_days} days</dd>
              <dt>News move</dt><dd>≥ {Math.round(data.analysis.jump_threshold * 100)} points within {data.analysis.jump_window_minutes} min</dd>
              <dt>“Before the news”</dt><dd>up to {data.analysis.pre_jump_hours} h before a move</dd>
              <dt>Co-betting</dt><dd>same side within {data.analysis.co_bet_window_minutes} min, on ≥ {data.analysis.min_co_bet_markets} markets, crowds ≤ {data.analysis.max_window_wallets}</dd>
              <dt>Fresh wallet</dt><dd>big bet within {data.analysis.fresh_wallet_hours} h of first funding</dd>
              <dt>Exchange detection</dt><dd>address linked to ≥ {data.analysis.hub_min_fanout} tracked wallets</dd>
              <dt>Funding trace</dt><dd>{data.onchain.trace_depth} hops, wallets above ${data.onchain.min_wallet_volume_usd}</dd>
            </dl>
          </Card>
        </div>
      </div>
    </div>
  );
}
