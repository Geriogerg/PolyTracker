import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cents, date, duration, severity, short, usd } from "../lib/format";
import type { JumpT, TradeT } from "../lib/types";

/* --------------------------------------------------------------- helpers */
function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(Math.max(260, Math.floor(e[0].contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const SEV_FILL = { low: "var(--low)", elevated: "var(--warn)", high: "var(--crit)" } as const;

function timeTicks(t0: number, t1: number, n = 6): number[] {
  const span = t1 - t0;
  const steps = [3600, 6 * 3600, 86400, 2 * 86400, 7 * 86400, 14 * 86400, 30 * 86400, 60 * 86400, 90 * 86400];
  const step = steps.find((s) => span / s <= n) ?? steps[steps.length - 1];
  const out: number[] = [];
  let t = Math.ceil(t0 / step) * step;
  while (t <= t1) {
    out.push(t);
    t += step;
  }
  return out;
}

function fmtTick(t: number, span: number): string {
  const d = new Date(t * 1000);
  if (span < 3 * 86400) return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** Column with a 4px rounded data-end and a square base. */
function colPath(x: number, y: number, w: number, h: number, r = 4): string {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

type Tip = { x: number; y: number; body: React.ReactNode } | null;

function TipBox({ tip, width }: { tip: Tip; width: number }) {
  if (!tip) return null;
  const left = tip.x > width - 220 ? tip.x - 210 : tip.x + 14;
  return (
    <div className="tip" style={{ left, top: Math.max(0, tip.y - 10) }}>
      {tip.body}
    </div>
  );
}

/* ------------------------------------------------------------ price chart */
export function PriceChart({
  series, jumps, trades, outcomes, preHours = 12, height = 320,
}: {
  series: { ts: number; p: number }[];
  jumps: JumpT[];
  trades: TradeT[];
  outcomes: string[];
  preHours?: number;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const [cross, setCross] = useState<number | null>(null);
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const nav = useNavigate();
  const m = { l: 44, r: 16, t: 26, b: 28 };
  const iw = width - m.l - m.r;
  const ih = height - m.t - m.b;

  const full = useMemo(() => {
    const ts = [...series.map((s) => s.ts), ...trades.map((t) => t.ts)];
    return [Math.min(...ts), Math.max(...ts)] as [number, number];
  }, [series, trades]);
  const [t0, t1] = zoom ?? full;
  const vis = useMemo(() => {
    const inR = (t: number) => t >= t0 && t <= t1;
    return { series: series.filter((s) => inR(s.ts)), trades: trades.filter((t) => inR(t.ts)) };
  }, [series, trades, t0, t1]);
  if (!series.length) return <div className="empty">No price history yet.</div>;
  const zoomTo = (j: JumpT) =>
    setZoom([j.start_ts - preHours * 2 * 3600, Math.min(full[1], j.end_ts + 8 * 3600)]);
  const x = (t: number) => m.l + ((t - t0) / Math.max(1, t1 - t0)) * iw;
  const y = (p: number) => m.t + (1 - p) * ih;
  const path = vis.series.map((s, i) => `${i ? "L" : "M"}${x(s.ts).toFixed(1)},${y(s.p).toFixed(1)}`).join("");
  const maxUsd = Math.max(1, ...trades.map((t) => t.usd));
  const rad = (u: number) => 4 + 8 * Math.sqrt(u / maxUsd);
  const ticks = timeTicks(t0, t1);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = e.clientX - box.left;
    const t = t0 + ((px - m.l) / iw) * (t1 - t0);
    const ser = vis.series;
    let lo = 0, hi = ser.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (ser[mid].ts < t) lo = mid + 1;
      else hi = mid;
    }
    const s = ser[lo];
    if (!s || px < m.l || px > width - m.r) {
      setCross(null);
      setTip(null);
      return;
    }
    setCross(lo);
    setTip({
      x: x(s.ts), y: y(s.p),
      body: (
        <>
          <strong>{cents(s.p)}</strong>
          <span className="k">“{outcomes[0] ?? "Yes"}” · {date(s.ts)}</span>
        </>
      ),
    });
  };

  return (
    <div className="chart" ref={ref}>
      <div className="legend-row">
        <span><svg width="18" height="4"><line x1="0" y1="2" x2="18" y2="2" stroke="var(--accent)" strokeWidth="2" /></svg>Price of “{outcomes[0] ?? "Yes"}”</span>
        <span><i className="legend-dot" style={{ background: SEV_FILL.high }} />Wallet score ≥ 70</span>
        <span><i className="legend-dot" style={{ background: SEV_FILL.elevated }} />40–69</span>
        <span><i className="legend-dot" style={{ background: SEV_FILL.low }} />Other large trades</span>
        <span><i style={{ width: 14, height: 10, background: "var(--accent-soft)", display: "inline-block", borderRadius: 2 }} />{preHours} h before a detected move</span>
        {jumps.length > 0 && !zoom && <span className="muted">Click a move to zoom in</span>}
        {zoom && <button className="linkish" onClick={() => setZoom(null)}>← Show full history</button>}
        {!zoom && jumps.length > 0 && jumps.slice(-3).map((j) => (
          <button key={j.id} className="linkish" onClick={() => zoomTo(j)}>Zoom: {date(j.start_ts, false)}</button>
        ))}
      </div>
      <svg height={height} onPointerMove={onMove} onPointerLeave={() => { setCross(null); setTip(null); }} role="img"
        aria-label={`Price history with ${jumps.length} detected moves and ${trades.length} highlighted trades`}>
        {[0, 0.25, 0.5, 0.75, 1].map((p) => (
          <g key={p}>
            <line className="gridline" x1={m.l} x2={width - m.r} y1={y(p)} y2={y(p)} />
            <text x={m.l - 8} y={y(p) + 4} textAnchor="end">{Math.round(p * 100)}¢</text>
          </g>
        ))}
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={height - 8} textAnchor="middle">{fmtTick(t, t1 - t0)}</text>
        ))}
        {jumps.filter((j) => j.end_ts >= t0 && j.start_ts <= t1).map((j) => {
          const xa = x(Math.max(t0, j.start_ts - preHours * 3600));
          const xs = x(j.start_ts);
          const xe = x(Math.min(j.end_ts, t1));
          return (
            <g key={j.id} onClick={(e) => { e.stopPropagation(); zoomTo(j); }} style={{ cursor: "zoom-in" }}>
              <rect x={xa} y={m.t} width={Math.max(1, xs - xa)} height={ih} fill="var(--accent-soft)" />
              <rect x={xs} y={m.t} width={Math.max(2, xe - xs)} height={ih} fill="var(--ink-3)" opacity={0.18} />
              <text x={xs} y={m.t - 8} textAnchor="middle" style={{ fill: "var(--ink-2)" }}>
                {j.direction > 0 ? "▲" : "▼"} {Math.round(Math.abs(j.p_after - j.p_before) * 100)} pts
              </text>
            </g>
          );
        })}
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {cross !== null && vis.series[cross] && (
          <line x1={x(vis.series[cross].ts)} x2={x(vis.series[cross].ts)} y1={m.t} y2={m.t + ih} stroke="var(--ink-3)" strokeWidth={1} />
        )}
        {vis.trades.map((t) => {
          const p0 = t.outcome_index === 0 ? t.price : 1 - t.price;
          const sev = severity(t.score);
          const cx = x(t.ts), cy = y(p0), r = rad(t.usd);
          const show = (e: React.SyntheticEvent) => {
            e.stopPropagation();
            setTip({
              x: cx, y: cy,
              body: (
                <>
                  <strong>{usd(t.usd)}</strong>
                  <div>{t.side === "BUY" ? "Bought" : "Sold"} “{t.outcome ?? outcomes[t.outcome_index]}” at {cents(t.price)}</div>
                  <div className="k">{t.name ?? short(t.wallet)} · score {t.score?.toFixed(0) ?? "—"}</div>
                  <div className="k">{date(t.ts)}</div>
                </>
              ),
            });
          };
          return (
            <g key={t.seq} onPointerMove={show} onFocus={show} onClick={() => nav(`/wallets/${t.wallet}`)}
              tabIndex={0} role="button" aria-label={`${usd(t.usd)} trade by ${t.name ?? t.wallet}`} style={{ cursor: "pointer" }}>
              <circle cx={cx} cy={cy} r={r + 8} fill="transparent" />
              <circle cx={cx} cy={cy} r={r} fill={SEV_FILL[sev]} stroke="var(--surface)" strokeWidth={2} opacity={0.92} />
            </g>
          );
        })}
      </svg>
      <TipBox tip={tip} width={width} />
    </div>
  );
}

/* -------------------------------------------------------------- histogram */
export function ScoreHistogram({ bins, height = 150 }: { bins: number[]; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const m = { l: 40, r: 8, t: 12, b: 24 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const max = Math.max(1, ...bins);
  const logMax = Math.log10(max + 1);
  const band = iw / Math.max(1, bins.length);
  const bw = Math.min(24, band - 4);
  const yv = (c: number) => (Math.log10(c + 1) / logMax) * ih;
  return (
    <div className="chart" ref={ref}>
      <svg height={height} role="img" aria-label="Number of wallets per score band (log scale)">
        {[1, 10, 100, 1000, 10000].filter((v) => v <= max * 1.2).map((v) => (
          <g key={v}>
            <line className="gridline" x1={m.l} x2={width - m.r} y1={m.t + ih - yv(v)} y2={m.t + ih - yv(v)} />
            <text x={m.l - 6} y={m.t + ih - yv(v) + 4} textAnchor="end">{v >= 1000 ? `${v / 1000}K` : v}</text>
          </g>
        ))}
        <line className="axis" x1={m.l} x2={width - m.r} y1={m.t + ih} y2={m.t + ih} />
        {bins.map((c, i) => {
          const sev = severity(i * 10);
          const bx = m.l + i * band + (band - bw) / 2;
          const h = yv(c);
          const show = () =>
            setTip({ x: bx + bw / 2, y: m.t + ih - h, body: <><strong>{c.toLocaleString()}</strong><span className="k">wallets scoring {i * 10}–{i * 10 + 9}</span></> });
          return (
            <g key={i} onPointerMove={show} onFocus={show} onPointerLeave={() => setTip(null)} tabIndex={0}>
              <rect x={m.l + i * band} y={m.t} width={band} height={ih} fill="transparent" />
              <path d={colPath(bx, m.t + ih - h, bw, h)} fill={SEV_FILL[sev]} />
              {i % 2 === 0 && <text x={m.l + i * band} y={height - 6} textAnchor="middle">{i * 10}</text>}
            </g>
          );
        })}
        <text x={width - m.r} y={height - 6} textAnchor="end">100</text>
      </svg>
      <TipBox tip={tip} width={width} />
    </div>
  );
}

/* -------------------------------------------------------------- sparkline */
export function Sparkline({ values, width = 160, height = 36, label }: { values: number[]; width?: number; height?: number; label: string }) {
  if (values.length < 2) return <span className="muted small">not enough runs yet</span>;
  const max = 100;
  const x = (i: number) => 4 + (i / (values.length - 1)) * (width - 8);
  const y = (v: number) => 4 + (1 - v / max) * (height - 8);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = values[values.length - 1];
  return (
    <svg className="spark" width={width} height={height} role="img" aria-label={`${label}: latest ${last.toFixed(0)}`}>
      <line x1={4} x2={width - 4} y1={y(70)} y2={y(70)} stroke="var(--grid)" strokeWidth={1} />
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(last)} r={4} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
    </svg>
  );
}

/* ---------------------------------------------------------- bet timeline */
export function BetTimeline({
  trades, markets, height: rowH = 44,
}: {
  trades: TradeT[];
  markets: Record<string, { question: string; outcomes: string[]; jumps: JumpT[] }>;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const nav = useNavigate();
  const lanes = useMemo(() => {
    const by: Record<string, TradeT[]> = {};
    trades.filter((t) => t.side === "BUY").forEach((t) => (by[t.condition_id] ??= []).push(t));
    return Object.entries(by)
      .map(([cid, ts]) => ({ cid, trades: ts, wallets: new Set(ts.map((t) => t.wallet)).size, usd: ts.reduce((a, t) => a + t.usd, 0) }))
      .sort((a, b) => b.wallets - a.wallets || b.usd - a.usd)
      .slice(0, 8);
  }, [trades]);
  const memberIdx = useMemo(() => {
    const ws = [...new Set(trades.map((t) => t.wallet))].sort();
    return Object.fromEntries(ws.map((w, i) => [w, i]));
  }, [trades]);
  if (!lanes.length) return <div className="empty">No bets.</div>;
  const nMembers = Math.max(1, Object.keys(memberIdx).length);
  const all = lanes.flatMap((l) => l.trades.map((t) => t.ts));
  const jumpTs = lanes.flatMap((l) => (markets[l.cid]?.jumps ?? []).map((j) => j.start_ts));
  const t0 = Math.min(...all, ...jumpTs) - 86400, t1 = Math.max(...all, ...jumpTs) + 86400;
  const labelW = Math.min(260, width * 0.32);
  const m = { l: labelW + 12, r: 14, t: 6, b: 26 };
  const iw = width - m.l - m.r;
  const height = m.t + m.b + lanes.length * rowH;
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0)) * iw;
  const maxUsd = Math.max(1, ...trades.map((t) => t.usd));
  const ticks = timeTicks(t0, t1, Math.max(3, Math.floor(iw / 110)));
  // each member gets its own sub-row inside a lane, so simultaneous bets stack visibly
  const spread = Math.min(4, (rowH - 12) / nMembers);
  const yOff = (w: string) => ((memberIdx[w] ?? 0) - (nMembers - 1) / 2) * spread;

  return (
    <div className="chart" ref={ref}>
      <div className="legend-row">
        <span><i className="legend-dot" style={{ background: "var(--accent)" }} />A member’s buy (size = $, one sub-row per member)</span>
        <span><svg width="14" height="12"><line x1="7" x2="7" y1="0" y2="12" stroke="var(--crit)" strokeWidth="2" /></svg>Detected price move (the news)</span>
      </div>
      <svg height={height} role="img" aria-label="Member bets per market over time, with detected news moves">
        {ticks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={x(t)} x2={x(t)} y1={m.t} y2={height - m.b} />
            <text x={x(t)} y={height - 8} textAnchor="middle">{fmtTick(t, t1 - t0)}</text>
          </g>
        ))}
        {lanes.map((l, i) => {
          const cy = m.t + i * rowH + rowH / 2;
          const mk = markets[l.cid];
          const q = mk?.question ?? l.cid.slice(0, 10);
          const maxChars = Math.floor(labelW / 6.8);
          return (
            <g key={l.cid} className="timeline-lane">
              <line className="gridline" x1={m.l} x2={width - m.r} y1={cy} y2={cy} />
              <text x={0} y={cy - 3}>{q.length > maxChars ? q.slice(0, maxChars - 1) + "…" : q}</text>
              <text x={0} y={cy + 12} style={{ fill: "var(--ink-3)", font: "11px var(--mono)" }}>{l.wallets} wallets · {usd(l.usd)}</text>
              {l.trades.map((t) => {
                const r = 2.5 + 5 * Math.sqrt(t.usd / maxUsd);
                const ty = cy + yOff(t.wallet);
                const j = (mk?.jumps ?? []).find((jj) => jj.start_ts > t.ts && jj.start_ts - t.ts < 3 * 86400);
                const show = () =>
                  setTip({
                    x: x(t.ts), y: ty,
                    body: (
                      <>
                        <strong>{usd(t.usd)}</strong>
                        <div>“{t.outcome ?? mk?.outcomes?.[t.outcome_index]}” at {cents(t.price)}</div>
                        <div className="k">{short(t.wallet)} · {date(t.ts)}</div>
                        {j && <div className="k">{duration((j.start_ts - t.ts) / 3600)} before the move</div>}
                      </>
                    ),
                  });
                return (
                  <g key={t.seq} onPointerMove={show} onFocus={show} onPointerLeave={() => setTip(null)}
                    onClick={() => nav(`/wallets/${t.wallet}`)} tabIndex={0} style={{ cursor: "pointer" }}>
                    <circle cx={x(t.ts)} cy={ty} r={r + 6} fill="transparent" />
                    <circle cx={x(t.ts)} cy={ty} r={r} fill="var(--accent)" fillOpacity={0.8} stroke="var(--surface)" strokeWidth={1.5} />
                  </g>
                );
              })}
              {(mk?.jumps ?? []).map((j) => (
                <line key={j.id} x1={x(j.start_ts)} x2={x(j.start_ts)} y1={cy - rowH / 2 + 2} y2={cy + rowH / 2 - 2}
                  stroke="var(--crit)" strokeWidth={2} pointerEvents="none" />
              ))}
            </g>
          );
        })}
      </svg>
      <TipBox tip={tip} width={width} />
    </div>
  );
}
