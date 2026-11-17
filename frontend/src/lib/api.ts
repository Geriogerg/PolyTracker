import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  AlertT,
  ClusterDetail,
  ClusterSummary,
  GraphData,
  MarketDetail,
  MarketRow,
  Overview,
  Settings,
  Status,
  TradeT,
  WalletDetail,
  WalletSummary,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

const q = (params: Record<string, string | number | boolean | undefined>) => {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && s.set(k, String(v)));
  const str = s.toString();
  return str ? `?${str}` : "";
};

const opts = { placeholderData: keepPreviousData, staleTime: 15_000 };

export const useOverview = () =>
  useQuery({ queryKey: ["overview"], queryFn: () => req<Overview>("/overview"), refetchInterval: 30_000, ...opts });

export const useClusters = (minScore = 0) =>
  useQuery({ queryKey: ["clusters", minScore], queryFn: () => req<ClusterSummary[]>(`/clusters${q({ min_score: minScore })}`), ...opts });

export const useCluster = (key: string) =>
  useQuery({ queryKey: ["cluster", key], queryFn: () => req<ClusterDetail>(`/clusters/${key}`), retry: false });

export const useGraph = (minScore: number) =>
  useQuery({ queryKey: ["graph", minScore], queryFn: () => req<GraphData>(`/graph${q({ min_score: minScore })}`), ...opts });

export const useWallets = (sort: string, search: string) =>
  useQuery({
    queryKey: ["wallets", sort, search],
    queryFn: () => req<WalletSummary[]>(`/wallets${q({ sort, q: search, limit: 200 })}`),
    ...opts,
  });

export const useWallet = (addr: string) =>
  useQuery({ queryKey: ["wallet", addr], queryFn: () => req<WalletDetail>(`/wallets/${addr}`), retry: false });

export const useMarkets = (status: string, search: string) =>
  useQuery({
    queryKey: ["markets", status, search],
    queryFn: () => req<MarketRow[]>(`/markets${q({ status, q: search })}`),
    ...opts,
  });

export const useMarket = (cid: string) =>
  useQuery({ queryKey: ["market", cid], queryFn: () => req<MarketDetail>(`/markets/${cid}`), retry: false });

export const useFeed = (flaggedOnly: boolean) =>
  useQuery({
    queryKey: ["feed", flaggedOnly],
    queryFn: () => req<TradeT[]>(`/feed${q({ limit: 60, flagged_only: flaggedOnly })}`),
    ...opts,
  });

export const useAlerts = () =>
  useQuery({ queryKey: ["alerts"], queryFn: () => req<AlertT[]>("/alerts?limit=200"), refetchInterval: 60_000 });

export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: () => req<Settings>("/settings") });

export const useStatus = () =>
  useQuery({ queryKey: ["status"], queryFn: () => req<Status>("/status"), refetchInterval: 10_000 });

export function useRunAnalysis() {
  return useMutation({ mutationFn: () => req<{ ok: boolean }>("/analysis/run", { method: "POST" }) });
}

export function useAck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number | "all") =>
      req(id === "all" ? "/alerts/ack-all" : `/alerts/${id}/ack`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

export function useTestAlert() {
  return useMutation({ mutationFn: () => req("/alerts/test", { method: "POST" }) });
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Pick<Settings, "scope" | "alerts">>) =>
      req<Settings>("/settings", { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: (data) => qc.setQueryData(["settings"], data),
  });
}

export function useResetSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => req<Settings>("/settings", { method: "DELETE" }),
    onSuccess: (data) => qc.setQueryData(["settings"], data),
  });
}
