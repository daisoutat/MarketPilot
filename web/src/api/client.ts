/* Typed API client for the MarketPilot FastAPI backend.
   In dev, Vite proxies `/api` to http://localhost:8000 (see vite.config.ts).
   A `VITE_API_BASE` override supports deployed/static hosting. */
import type {
  AlertRulesPage,
  AlertsPage,
  AlertRuleItem,
  AnalyticsTree,
  ChatMessagesPage,
  ChatPredictionsPage,
  ChatPrefs,
  ChatPrefsPage,
  ChatSendResult,
  ComparePage,
  ForecastDetail,
  ForecastListPage,
  InventoryPage,
  MarginPage,
  MarketplacesPage,
  OrdersPage,
  ResearchPage,
  SegmentPage,
  Series,
  Summary,
  SyncPage,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
const TOKEN_KEY = "mp.token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, params?: Query): string {
  const url = `${BASE}${path}`;
  if (!params) return url;
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") qs.append(key, String(value));
  }
  const q = qs.toString();
  return q ? `${url}?${q}` : url;
}

function authHeader(): Record<string, string> {
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function request<T>(path: string, params?: Query, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(buildUrl(path, params), {
      ...init,
      headers: { Accept: "application/json", ...authHeader(), ...(init?.headers ?? {}) },
    });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : "network error");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = (body && (body.detail || body.message)) || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, String(detail));
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

const D = "/api/v1/dashboard";

export const api = {
  summary: (days: number) => request<Summary>(`${D}/summary`, { days }),
  series: (days: number) => request<Series>(`${D}/series`, { days }),
  orders: (params: { market?: string; status?: string; q?: string; limit?: number; offset?: number }) =>
    request<OrdersPage>(`${D}/orders`, params),
  inventory: (params: { market?: string; q?: string; limit?: number }) =>
    request<InventoryPage>(`${D}/inventory`, params),
  margin: (params: { days?: number; market?: string; min_units?: number; q?: string; limit?: number }) =>
    request<MarginPage>(`${D}/margin`, params),
  research: (params: { market?: string; q?: string; limit?: number }) =>
    request<ResearchPage>(`${D}/research`, params),
  compare: (params: { market?: string; asin?: string; q?: string; limit?: number }) =>
    request<ComparePage>(`${D}/compare`, params),
  sync: (limit = 12) => request<SyncPage>(`${D}/sync`, { limit }),
  marketplaces: () => request<MarketplacesPage>(`${D}/marketplaces`),
  health: () => request<{ status: string }>(`${D}/healthz`),
  liveness: () => request<{ status: string }>("/healthz"),
  me: () => request<{ user: { email: string; name: string; role: string } }>("/api/v1/me"),

  alerts: (params: { kind?: string; market?: string; status?: "open" | "resolved" | "all"; limit?: number }) =>
    request<AlertsPage>("/api/v1/alerts", params),
  resolveAlert: (id: number) =>
    request<{ id: number; resolved_at: string; resolved_by: string }>(`/api/v1/alerts/${id}/resolve`, undefined, {
      method: "POST",
    }),
  alertRules: () => request<AlertRulesPage>("/api/v1/alerts/rules"),
  createAlertRule: (body: { kind: string; threshold: number; cooldown_hours?: number; enabled?: boolean }) =>
    request<AlertRuleItem>("/api/v1/alerts/rules", undefined, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  updateAlertRule: (id: number, body: Partial<{ threshold: number; cooldown_hours: number; enabled: boolean }>) =>
    request<AlertRuleItem>(`/api/v1/alerts/rules/${id}`, undefined, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  tree: (params: { days?: number; market?: string }) => request<AnalyticsTree>("/api/v1/analytics/tree", params),
  segment: (familyId: number, params: { days?: number; market?: string; limit?: number }) =>
    request<SegmentPage>(`/api/v1/analytics/segment/${familyId}`, params),
  forecasts: (params: { days?: number; market?: string; limit?: number }) =>
    request<ForecastListPage>("/api/v1/analytics/forecast", params),
  forecastDetail: (productId: number) => request<ForecastDetail>(`/api/v1/analytics/forecast/${productId}`),

  chatSend: (message: string, body: { session_id?: string; lang?: string }) =>
    request<ChatSendResult>("/api/v1/chat/send", undefined, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, ...body }),
    }),
  chatMessages: (sessionId: string, limit?: number) =>
    request<ChatMessagesPage>("/api/v1/chat/messages", { session_id: sessionId, limit }),
  chatPrefs: () => request<ChatPrefsPage>("/api/v1/chat/prefs"),
  updateChatPrefs: (body: Partial<ChatPrefs>) =>
    request<ChatPrefsPage>("/api/v1/chat/prefs", undefined, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  chatPredictions: (limit = 6) => request<ChatPredictionsPage>("/api/v1/chat/predictions", { limit }),
  chatDeleteSession: (sessionId: string) =>
    request<{ deleted: boolean; session_id: string }>(`/api/v1/chat/sessions/${sessionId}`, undefined, {
      method: "DELETE",
    }),
};

export type Api = typeof api;
