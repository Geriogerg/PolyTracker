import { useEffect, useMemo, useRef, useState } from "react";
import { forceCollide, forceX, forceY } from "d3-force";
import ForceGraph2D, { ForceGraphMethods } from "react-force-graph-2d";
import { severity, short, usd } from "../lib/format";
import type { GraphData } from "../lib/types";

const KIND_PRIORITY = ["direct_transfer", "funding_shared", "withdrawal_shared", "co_bet"] as const;
const KIND_VAR: Record<string, string> = {
  direct_transfer: "--edge-transfer",
  funding_shared: "--edge-funding",
  withdrawal_shared: "--edge-withdrawal",
  co_bet: "--edge-cobet",
};

type N = GraphData["nodes"][number] & { x?: number; y?: number };
type L = { source: string | N; target: string | N; weight: number; kinds: string[]; kind: string };

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

function readVars(): Record<string, string> {
  const cs = getComputedStyle(document.documentElement);
  const get = (v: string) => cs.getPropertyValue(v).trim();
  return {
    surface: get("--surface"), ink: get("--ink"), ink3: get("--ink-3"), low: get("--low"),
    warn: get("--warn"), crit: get("--crit"),
    ...Object.fromEntries(Object.entries(KIND_VAR).map(([k, v]) => [k, get(v)])),
  };
}

export function ClusterGraph({
  data, focus, kinds, theme, onNode,
}: {
  data: GraphData;
  focus: string | null;
  kinds: Set<string>;
  theme: string;
  onNode: (id: string) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const fg = useRef<ForceGraphMethods<N, L>>();
  const [size, setSize] = useState({ w: 800, h: 600 });
  const colors = useMemo(readVars, [theme]);

  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver((e) => setSize({ w: e[0].contentRect.width, h: e[0].contentRect.height }));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const graph = useMemo(() => {
    const links: L[] = data.links
      .map((l) => ({ ...l, kind: KIND_PRIORITY.find((k) => l.kinds.includes(k)) ?? "co_bet" }))
      .filter((l) => l.kinds.some((k) => kinds.has(k)));
    return { nodes: data.nodes.map((n) => ({ ...n })) as N[], links };
  }, [data, kinds]);

  const maxVol = useMemo(() => Math.max(1, ...data.nodes.map((n) => n.volume)), [data]);
  const radius = (n: N) => 3 + 11 * Math.sqrt(n.volume / maxVol);

  const fitted = useRef(false);
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    fitted.current = false;
    g.d3Force("charge")?.strength(-70).distanceMax(260);
    g.d3Force("link")?.distance((l: L) => (l.kind === "co_bet" ? 70 : 46));
    // pull disconnected clusters toward the middle instead of letting them drift off
    g.d3Force("x", forceX(0).strength(0.07));
    g.d3Force("y", forceY(0).strength(0.07));
    g.d3Force("collide", forceCollide((n: N) => radius(n) + 4));
    g.d3ReheatSimulation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  const fit = () => {
    if (!fg.current) return;
    if (focus) fg.current.zoomToFit(600, 80, (n: N) => n.cluster === focus);
    else fg.current.zoomToFit(500, 70);
  };
  useEffect(() => {
    const t = setTimeout(fit, 60);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  const sevColor = (s: number) => {
    const v = severity(s);
    return v === "high" ? colors.crit : v === "elevated" ? colors.warn : colors.low;
  };

  return (
    <div ref={wrap} style={{ position: "absolute", inset: 0 }}>
      <ForceGraph2D
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={graph}
        backgroundColor="rgba(0,0,0,0)"
        cooldownTicks={220}
        nodeId="id"
        nodeVal={(n: N) => radius(n) ** 2 / 4}
        nodeLabel={(n: N) =>
          `<div class="tip" style="position:static"><strong>${esc(n.name ?? short(n.id))}</strong>` +
          `score ${n.score.toFixed(0)} · ${esc(usd(n.volume))} wagered<br/><span class="k">click to open wallet</span></div>`
        }
        nodeCanvasObject={(n: N, ctx, scale) => {
          const r = radius(n);
          const dim = focus && n.cluster !== focus;
          ctx.globalAlpha = dim ? 0.15 : 1;
          ctx.beginPath();
          ctx.arc(n.x!, n.y!, r + 1.5, 0, 2 * Math.PI);
          ctx.fillStyle = colors.surface;
          ctx.fill();
          ctx.beginPath();
          ctx.arc(n.x!, n.y!, r, 0, 2 * Math.PI);
          ctx.fillStyle = sevColor(n.score);
          ctx.fill();
          if (!dim && (scale > 3 || (n.score >= 70 && scale > 1.9))) {
            ctx.font = `${11 / scale}px IBM Plex Mono, monospace`;
            ctx.fillStyle = colors.ink;
            ctx.textAlign = "center";
            ctx.fillText(n.name ?? short(n.id, 3), n.x!, n.y! + r + 11 / scale);
          }
          ctx.globalAlpha = 1;
        }}
        nodePointerAreaPaint={(n: N, color, ctx) => {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(n.x!, n.y!, radius(n) + 5, 0, 2 * Math.PI);
          ctx.fill();
        }}
        linkColor={(l: L) => {
          const s = l.source as N, t = l.target as N;
          const dim = focus && (s.cluster !== focus || t.cluster !== focus);
          const c = colors[l.kind] || colors.ink3;
          return dim ? c + "22" : c;
        }}
        linkWidth={(l: L) => (l.kind === "co_bet" ? 1 : Math.min(1.2 + l.weight / 12, 3))}
        linkLineDash={(l: L) => (l.kind === "co_bet" ? [3, 3] : null)}
        onNodeClick={(n: N) => onNode(n.id)}
        onEngineStop={() => {
          if (!fitted.current) {
            fitted.current = true;
            fit();
          }
        }}
        enableNodeDrag
      />
    </div>
  );
}
