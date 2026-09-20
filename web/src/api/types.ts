/* DTO types mirroring the FastAPI dashboard responses (api/app/routers/dashboard.py). */

export interface SummaryMarket {
  market: string;
  orders: number;
  units: number;
  gross: number;
}

export interface StatusCount {
  status: string;
  count: number;
}

export interface Summary {
  window_days: number;
  total: { orders: number; units: number; gross: number };
  by_market: SummaryMarket[];
  by_status: StatusCount[];
}

export interface SeriesPoint {
  date: string;
  orders: number;
  units: number;
  gross: number;
}

export interface Series {
  days: number;
  points: SeriesPoint[];
}

export interface OrderRow {
  id: number;
  amazon_order_id: string;
  market: string;
  status: string;
  purchase_date: string | null;
  city: string | null;
  state_or_region: string | null;
  buyer_name: string | null;
  units: number;
  gross: number;
}

export interface OrdersPage {
  total: number;
  limit: number;
  offset: number;
  orders: OrderRow[];
}

export interface InventoryRow {
  asin: string;
  title: string;
  brand: string;
  market: string;
  price: number;
  currency: string;
  quantity: number;
  captured_at: string | null;
}

export interface InventoryPage {
  limit: number;
  rows: InventoryRow[];
}

export interface MarginRow {
  asin: string;
  title: string;
  market: string;
  currency: string;
  units: number;
  revenue: number;
  fees: number;
  net: number;
  margin_pct: number | null;
  avg_price: number | null;
}

export interface MarginPage {
  window_days: number;
  limit: number;
  rows: MarginRow[];
}

export interface ResearchRow {
  asin: string;
  title: string;
  market: string;
  keywords: string;
  price: number;
  currency: string;
  price_usd: number;
  buybox: number | null;
  sales_rank: number | null;
  captured_at: string | null;
  last_snap_at: string | null;
}

export interface ResearchPage {
  limit: number;
  count: number;
  rows: ResearchRow[];
}

export interface CompareMarket {
  market: string;
  price: number;
  currency: string;
  price_usd: number;
  buybox: number | null;
  sales_rank: number | null;
  captured_at: string | null;
}

export interface CompareRow {
  asin: string;
  title: string;
  brand: string;
  markets: CompareMarket[];
  gap_usd: number | null;
}

export interface ComparePage {
  count: number;
  rows: CompareRow[];
}

export interface SyncRun {
  job: string;
  market: string | null;
  status: string;
  started: string | null;
  finished: string | null;
  rows: number;
  error: string;
}

export interface SyncPage {
  runs: SyncRun[];
}

export interface MarketplaceRow {
  code: string;
  marketplace_id: string;
  currency: string;
  iso: string;
}

export interface MarketplacesPage {
  marketplaces: MarketplaceRow[];
}

export type AlertSeverity = "info" | "warning" | "critical";

export interface AlertItem {
  id: number;
  kind: "price_drop" | "stock_out" | "margin_erosion" | string;
  severity: AlertSeverity;
  message: string;
  asin: string | null;
  market: string | null;
  meta: Record<string, unknown> | null;
  created_at: string | null;
  resolved_at: string | null;
  resolved_by: string;
}

export interface AlertsPage {
  status: string;
  count: number;
  rows: AlertItem[];
}

export interface AlertRuleItem {
  id: number;
  kind: "price_drop" | "stock_out" | "margin_erosion";
  threshold: number;
  cooldown_hours: number;
  enabled: boolean;
}

export interface AlertRulesPage {
  rules: AlertRuleItem[];
}

export interface CategoryNode {
  id: number;
  kind: "category" | "niche" | "family";
  name: string;
  path: string;
  depth: number;
  child_count: number;
  product_count: number;
  units: number;
  revenue: number;
  net: number;
  margin_pct: number | null;
  avg_rank: number | null;
  avg_price: number | null;
  currency: string;
}

export interface AnalyticsTree {
  window_days: number;
  market: string | null;
  count: number;
  nodes: CategoryNode[];
}

export interface SegmentProduct {
  product_id: number;
  asin: string;
  title: string;
  brand: string;
  units: number;
  revenue: number;
  net: number;
  margin_pct: number | null;
  rank: number | null;
  price: number | null;
  currency: string;
}

export interface SegmentPage {
  family_id: number;
  exists: boolean;
  name: string;
  path: string;
  rows: SegmentProduct[];
}

export interface ForecastRow {
  product_id: number;
  asin: string;
  title: string;
  brand: string;
  generated_at: string | null;
  unit: string;
  window_days: number;
  horizon: number;
  mean_per_day: number | null;
  trend_per_week_pct: number | null;
  volatility: number | null;
  rmse: number | null;
  horizon_total: number;
}

export interface ForecastListPage {
  count: number;
  unit: string;
  rows: ForecastRow[];
}

export interface ForecastSeries {
  dates: string[];
  yhat: number[];
  lo: number[];
  hi: number[];
}

export interface ForecastHistory {
  dates: string[];
  values: number[];
}

export interface ForecastDetail {
  product_id: number;
  asin: string;
  title: string;
  brand: string;
  generated_at: string | null;
  unit: string;
  window_days: number;
  horizon: number;
  model: string;
  params: {
    model: string;
    mean_per_day: number | null;
    trend_per_week_pct: number | null;
    volatility: number | null;
    rmse: number | null;
    seasonal: Record<string, number> | null;
  } | null;
  history: ForecastHistory | null;
  points: ForecastSeries | null;
  explain: string | null;
}

/* ------------------------- Assistant (Phase 7) ------------------------- */

export interface ChatPrefs {
  market: string; // all | US | CA
  language: string; // en | fr
  currency: string;
  digest: string; // daily | weekly | off
  quick: string[];
}

export interface ChatPrefsPage {
  prefs: ChatPrefs;
}

export interface PredictionItem {
  product_id: number;
  asin: string;
  title: string;
  brand: string;
  mean_per_day: number | null;
  trend_per_week_pct: number | null;
  volatility: number | null;
  rmse: number | null;
  next_30d: number;
  yhat: number[];
}

export interface ChatInsights {
  count: number;
  generated_at: string | null;
  gainers: PredictionItem[];
  decliners: PredictionItem[];
  top_next_30d: PredictionItem[];
}

export interface ChatPredictionsPage {
  insights: ChatInsights;
  generated_at: string | null;
}

/** Polymorphic sidecar data attached to an assistant reply. */
export interface ChatPayload {
  [key: string]: unknown;
}

export interface ChatReply {
  intent: string;
  text: string;
  data: ChatPayload | null;
  created_at: string;
}

export interface ChatSendResult {
  session_id: string;
  reply: ChatReply;
  prefs: ChatPrefs;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  intent: string;
  text: string;
  data: ChatPayload | null;
  created_at: string | null;
}

export interface ChatMessagesPage {
  session_id: string;
  messages: ChatMessage[];
}
