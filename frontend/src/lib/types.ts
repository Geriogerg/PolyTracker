export type Component = { key: string; label: string; points: number; max: number; detail?: string };

export type ClusterSummary = {
  key: string;
  score: number;
  size: number;
  volume: number;
  pnl: number;
  top_market: TopMarket | null;
  components: Component[];
  members: string[];
  first_ts: number | null;
  last_ts: number | null;
  n_structural: number;
};

export type TopMarket = {
  condition_id: string;
  question: string;
  slug: string | null;
  n_members: number;
  usd: number;
  backed: string;
  first_ts: number;
};

export type WalletSummary = {
  wallet: string;
  name: string | null;
  score: number;
  cluster_key: string | null;
  volume: number;
  pnl: number;
  win_rate: number | null;
  resolved: number;
  n_markets: number;
  timing_hits: number;
  last_ts: number | null;
  components: Component[];
};

export type Run = {
  id: number;
  started_at: number;
  finished_at: number | null;
  status: string;
  duration_s: number | null;
  wallets_scored: number | null;
  clusters: number | null;
  edges: number | null;
  jumps: number | null;
  trades: number | null;
  error: string | null;
};

export type AlertT = {
  id: number;
  created_at: number;
  kind: "cluster" | "wallet" | "trade";
  ref: string;
  score: number;
  title: string;
  message: string;
  channels: string[];
  acknowledged: boolean;
};

export type WorkerStatus = {
  task: string;
  heartbeat: number;
  last_ok: number | null;
  last_error: string | null;
  last_error_at: number | null;
  counters: Record<string, number | string>;
};

export type JumpT = {
  id: number;
  condition_id: string;
  start_ts: number;
  end_ts: number;
  p_before: number;
  p_after: number;
  direction: number;
};

export type Overview = {
  demo: boolean;
  run: Run | null;
  totals: { markets: number; trades: number; wallets: number; trades_24h: number; volume_24h: number };
  top_clusters: ClusterSummary[];
  top_wallets: WalletSummary[];
  alerts: AlertT[];
  jumps: (JumpT & { question: string; n_buyers: number; n_flagged: number; usd_before: number })[];
  score_histogram: number[];
  workers: WorkerStatus[];
};

export type TradeT = {
  seq: number;
  wallet: string;
  condition_id: string;
  outcome: string | null;
  outcome_index: number;
  side: "BUY" | "SELL";
  price: number;
  size: number;
  usd: number;
  ts: number;
  tx_hash: string | null;
  source: string;
  score?: number | null;
  cluster_key?: string | null;
  name?: string | null;
  question?: string | null;
};

export type Evidence = { kind: string; [k: string]: unknown };
export type EdgeT = { a: string; b: string; weight: number; kinds: string[]; evidence: Evidence[] };

export type MarketBrief = {
  condition_id: string;
  question: string;
  slug: string | null;
  outcomes: string[];
  resolved: boolean;
  winning_index: number | null;
  last_price: number | null;
};

export type Party = { address: string; kind: string; label: string | null };

export type ClusterDetail = {
  cluster: ClusterSummary & {
    components: Component[];
    stats: Record<string, any>;
    top_markets: TopMarket[];
    run_id: number;
    stale: boolean;
  };
  members: WalletSummary[];
  edges: EdgeT[];
  trades: TradeT[];
  markets: Record<string, MarketBrief & { jumps: JumpT[] }>;
  transfers: { tx_hash: string; from: Party; to: Party; amount: number; ts: number }[];
  history: { run_id: number; score: number; size: number }[];
};

export type WalletDetail = {
  wallet: {
    address: string;
    name: string | null;
    pseudonym: string | null;
    first_trade_ts: number | null;
    last_trade_ts: number | null;
    first_funded_ts: number | null;
    traced_at: number | null;
    trace_status: string | null;
  };
  score: { score: number; components: Component[]; stats: Record<string, any>; cluster_key: string | null } | null;
  cluster: ClusterSummary | null;
  history: { run_id: number; score: number }[];
  positions: {
    market: MarketBrief;
    buy_usd: number;
    sell_usd: number;
    main_outcome: number;
    shares: Record<string, number>;
    pnl: number | null;
    won: boolean | null;
    first_ts: number;
    last_ts: number;
  }[];
  trades: TradeT[];
  transfers: { tx_hash: string; direction: "in" | "out"; counterparty: Party; amount: number; ts: number }[];
  links: { wallet: string; weight: number; kinds: string[]; evidence: Evidence[]; score: number | null; name: string | null }[];
};

export type MarketRow = MarketBrief & {
  tags: string[];
  volume: number;
  closed: boolean;
  end_ts: number | null;
  closed_ts: number | null;
  event_title: string | null;
  n_trades: number;
  n_wallets: number;
  our_volume: number;
  last_trade_ts: number | null;
  n_jumps: number;
  n_flagged: number;
  backfilled: boolean;
};

export type Buyer = {
  wallet: string;
  usd: number;
  first_ts: number;
  avg_price: number;
  hours_before: number;
  score: number | null;
  cluster_key: string | null;
  name: string | null;
};

export type MarketDetail = {
  market: MarketRow & { token_ids: string[] };
  series: { ts: number; p: number }[];
  jumps: (JumpT & { buyers: Buyer[] })[];
  flagged_trades: TradeT[];
  top_buyers: { wallet: string; outcome_index: number; usd: number; score: number | null; cluster_key: string | null; name: string | null }[];
};

export type GraphData = {
  nodes: { id: string; score: number; cluster: string | null; volume: number; name: string | null }[];
  links: { source: string; target: string; weight: number; kinds: string[] }[];
  clusters: { key: string; score: number; size: number }[];
};

export type Scope = {
  mode: "tags" | "all" | "explicit";
  tags: string[];
  include_keywords: string[];
  exclude_keywords: string[];
  explicit_markets: string[];
  min_volume: number;
  max_markets: number;
  include_closed_days: number;
};

export type AlertSettings = {
  enabled: boolean;
  cluster_score: number;
  wallet_score: number;
  large_trade_usd: number;
  cooldown_hours: number;
};

export type Settings = {
  demo: boolean;
  scope: Scope;
  alerts: AlertSettings;
  analysis: Record<string, any>;
  ingestion: Record<string, any>;
  onchain: Record<string, any>;
  overridden: string[];
  integrations: { onchain: boolean; telegram: boolean; discord: boolean };
};

export type Status = {
  demo: boolean;
  time: number;
  workers: WorkerStatus[];
  runs: Run[];
  counts: Record<string, number>;
  analysis_requested_at: number;
};
