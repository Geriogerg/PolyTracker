export const short = (a: string, n = 4) => (a ? `${a.slice(0, 2 + n)}…${a.slice(-n)}` : "");

export function usd(x: number | null | undefined, signed = false): string {
  if (x == null || Number.isNaN(x)) return "—";
  const sign = x < 0 ? "−" : signed && x > 0 ? "+" : "";
  const a = Math.abs(x);
  const body =
    a >= 1e9 ? `${(a / 1e9).toFixed(2)}B` :
    a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` :
    a >= 1e4 ? `${(a / 1e3).toFixed(1)}k` :
    a >= 1e3 ? `${(a / 1e3).toFixed(2)}k` :
    a >= 1 ? a.toFixed(0) : a.toFixed(2);
  return `${sign}$${body}`;
}

export function num(x: number | null | undefined): string {
  if (x == null) return "—";
  const a = Math.abs(x);
  if (a >= 1e6) return `${(x / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(x / 1e3).toFixed(1)}k`;
  return x.toLocaleString("en-US");
}

export const pct = (x: number | null | undefined, d = 0) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
export const cents = (p: number | null | undefined) => (p == null ? "—" : `${(p * 100).toFixed(0)}¢`);

function span(d: number): string {
  if (d < 60) return `${Math.max(1, Math.round(d))}s`;
  if (d < 3600) return `${Math.round(d / 60)}m`;
  if (d < 86400) return `${Math.round(d / 3600)}h`;
  if (d < 86400 * 60) return `${Math.round(d / 86400)}d`;
  return `${Math.round(d / (86400 * 30))}mo`;
}

export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "—";
  const d = now - ts;
  // fresh trades can be a few seconds "in the future" (clock skew, a stale `now`)
  if (d < 0) return d > -120 ? "just now" : `in ${span(-d)}`;
  return `${span(d)} ago`;
}

export function date(ts: number | null | undefined, withTime = true): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: d.getFullYear() === new Date().getFullYear() ? undefined : "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export function duration(h: number): string {
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(h < 10 ? 1 : 0)} h`;
  return `${Math.round(h / 24)} d`;
}

export type Severity = "low" | "elevated" | "high";
export const severity = (s: number | null | undefined): Severity => {
  const r = Math.round(s ?? 0); // classify the number the user sees
  return r < 40 ? "low" : r < 70 ? "elevated" : "high";
};
export const severityLabel: Record<Severity, string> = { low: "Low", elevated: "Elevated", high: "High" };

export const KIND_LABEL: Record<string, string> = {
  funding_shared: "Shared funder",
  direct_transfer: "Direct transfer",
  withdrawal_shared: "Shared cash-out",
  co_bet: "Bet together",
  behavior: "Similar behaviour",
};

export const polymarketProfile = (a: string) => `https://polymarket.com/profile/${a}`;
export const polygonscan = (a: string) => `https://polygonscan.com/address/${a}`;
export const polygonTx = (h: string) => `https://polygonscan.com/tx/${h}`;
export const polymarketMarket = (slug: string | null) => (slug ? `https://polymarket.com/market/${slug}` : null);
