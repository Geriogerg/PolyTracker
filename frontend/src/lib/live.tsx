import { useQueryClient } from "@tanstack/react-query";
import { createContext, ReactNode, useContext, useEffect, useRef, useState } from "react";
import type { AlertT, Run, TradeT } from "./types";

type Live = {
  connected: boolean;
  trades: TradeT[];
  toasts: AlertT[];
  dismiss: (id: number) => void;
  lastRun: Run | null;
};

const LiveCtx = createContext<Live>({ connected: false, trades: [], toasts: [], dismiss: () => {}, lastRun: null });

/** One EventSource for the whole app: new trades, alerts and finished analysis runs. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [trades, setTrades] = useState<TradeT[]>([]);
  const [toasts, setToasts] = useState<AlertT[]>([]);
  const [lastRun, setLastRun] = useState<Run | null>(null);
  const buf = useRef<TradeT[]>([]);

  useEffect(() => {
    const es = new EventSource("/api/stream");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("trade", (e) => {
      try {
        buf.current.push(JSON.parse((e as MessageEvent).data));
      } catch {
        /* ignore malformed */
      }
    });
    es.addEventListener("alert", (e) => {
      const a = JSON.parse((e as MessageEvent).data) as AlertT;
      setToasts((t) => [a, ...t].slice(0, 3));
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      setTimeout(() => setToasts((t) => t.filter((x) => x.id !== a.id)), 12_000);
    });
    es.addEventListener("run", (e) => {
      setLastRun(JSON.parse((e as MessageEvent).data));
      for (const k of ["overview", "clusters", "graph", "wallets", "markets", "cluster", "wallet", "market", "status"])
        qc.invalidateQueries({ queryKey: [k] });
    });
    // batch trade updates so a burst renders once
    const flush = setInterval(() => {
      if (!buf.current.length) return;
      const incoming = buf.current.reverse();
      buf.current = [];
      setTrades((t) => [...incoming, ...t].slice(0, 120));
    }, 500);
    return () => {
      clearInterval(flush);
      es.close();
    };
  }, [qc]);

  return (
    <LiveCtx.Provider
      value={{ connected, trades, toasts, lastRun, dismiss: (id) => setToasts((t) => t.filter((x) => x.id !== id)) }}
    >
      {children}
    </LiveCtx.Provider>
  );
}

export const useLive = () => useContext(LiveCtx);
