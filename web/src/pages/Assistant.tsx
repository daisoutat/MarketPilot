/* Phase 7 — Intelligent assistant: deterministic NLU backed by the live API. */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  ChatMessage,
  ChatPayload,
  ChatPrefs,
  PredictionItem,
} from "../api/types";
import { BarChart, ForecastChart } from "../components/charts";
import { Card, DataTable, ErrorBox, Icon, Kpi, type Column } from "../components/ui";
import { useI18n, type TranslationKey } from "../i18n";

const SESSION_KEY = "mp.chat.session";

const PREF_DEFAULTS: ChatPrefs = {
  market: "all",
  language: "en",
  currency: "USD",
  digest: "daily",
  quick: [],
};

interface Exchange {
  id: number;
  role: "user" | "assistant";
  intent: string;
  text: string;
  data: ChatPayload | null;
  createdAt: string | null;
}

const QUICK: { key: TranslationKey; en: string; fr: string }[] = [
  { key: "assistant.quick.summary", en: "Give me the big picture status over the last 90 days", fr: "Donne-moi l'état des lieux sur 90 jours" },
  { key: "assistant.quick.orders", en: "Show me my recent orders", fr: "Montre-moi mes dernières commandes" },
  { key: "assistant.quick.margin", en: "What is my margin over the last 90 days", fr: "Quelle est ma marge sur les 90 derniers jours" },
  { key: "assistant.quick.forecast", en: "What does the forecast say for the coming 30 days", fr: "Quelle est la prévision de la demande" },
  { key: "assistant.quick.alerts", en: "Show me open alerts", fr: "Montre-moi les alertes ouvertes" },
  { key: "assistant.quick.inventory", en: "What is my inventory level", fr: "Quel est mon niveau de stock" },
  { key: "assistant.quick.compare", en: "Compare prices between US and CA", fr: "Compare les prix entre le Canada et les États-Unis" },
];

/* -------------------------------------------------- polymorphic data tools */
const str = (v: unknown): string => (typeof v === "string" ? v : v === null || v === undefined ? "" : String(v));
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const obj = (v: unknown): ChatPayload | null =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as ChatPayload) : null;
const arr = (v: unknown): ChatPayload[] | null => (Array.isArray(v) ? (v as ChatPayload[]) : null);

const fmtPct = (v: unknown): string => {
  const n = num(v);
  return n === null ? "—" : `${n.toFixed(1)}%`;
};

function asinKey(row: ChatPayload) {
  return `${str(row.product_id)}:${str(row.asin)}:${str(row.order_id)}`;
}

/* --------------------------------------------------------------- leading  */
function TrendValue({ value, digits = 1 }: { value: unknown; digits?: number }) {
  const n = num(value);
  if (n === null) return <span>—</span>;
  return (
    <span className={n >= 0 ? "pos" : "neg"}>
      {n >= 0 ? "+" : ""}
      {n.toFixed(digits)}
    </span>
  );
}

function PredictionRows({ rows }: { rows: (ChatPayload | PredictionItem)[] }) {
  const { t, formatNumber } = useI18n();
  const columns: Column<ChatPayload | PredictionItem>[] = [
    { header: t("common.asin"), render: (r) => <span className="mono">{str(r.asin)}</span> },
    { header: t("common.title"), render: (r) => str(r.title) || "—" },
    { header: t("assistant.colMean"), align: "right", render: (r) => formatNumber(num(r.mean_per_day), 1) },
    { header: t("assistant.trend"), align: "right", render: (r) => <TrendValue value={r.trend_per_week_pct} /> },
    { header: t("common.volatility"), align: "right", render: (r) => formatNumber(num(r.volatility), 1) },
    {
      header: t("assistant.next30"),
      align: "right",
      render: (r) => `${formatNumber(num(r.next_30d), 1)}`,
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.product_id}`}
      emptyMessage={t("assistant.emptyData")}
    />
  );
}

/* --------------------------------------------------------------- payload */
function ForecastPane({ data }: { data: ChatPayload }) {
  const { t } = useI18n();
  const chart = obj(data.chart);
  const lines = (obj(data.forecast) ?? {}) as ChatPayload;
  const createdAt = str(data.generated_at);
  const hist = chart ? obj(chart.history) : null;
  const histDates = (hist ? hist.dates : null) as string[] | null;
  const histValues = (hist ? hist.values : null) as number[] | null;
  const fcDates = chart ? (chart.dates as string[] | null) : null;
  const fcYhat = chart ? (chart.yhat as number[] | null) : null;
  const fcLo = chart ? (chart.lo as number[] | null) : null;
  const fcHi = chart ? (chart.hi as number[] | null) : null;
  const series: { label: string; actual?: number | null; forecast?: number | null; lo?: number | null; hi?: number | null }[] = [];
  if (histDates && histValues) {
    histDates.forEach((label, i) => {
      series.push({ label: label.slice(5), actual: histValues?.[i] ?? null });
    });
  }
  if (fcDates) {
    fcDates.forEach((label, i) => {
      series.push({
        label: label.slice(5),
        forecast: fcYhat?.[i] ?? null,
        lo: fcLo?.[i] ?? null,
        hi: fcHi?.[i] ?? null,
      });
    });
  }
  return (
    <>
      <div className="chat-kpis">
        <Kpi label={t("forecast.mean")} value={`${formatNum(num(lines.mean_per_day))}`} />
        <Kpi label={t("forecast.trend")} value={<TrendValue value={lines.trend_per_week_pct} />} />
        <Kpi label={t("assistant.next30")} value={`${formatNum(num(lines.next_30d))}`} />
      </div>
      {chart && series.length > 0 && (
        <ForecastChart series={series} formatValue={(v) => v.toFixed(1)} />
      )}
      {createdAt && <div className="muted" style={{ fontSize: 12 }}>{t("assistant.generated", { date: createdAt })}</div>}
    </>
  );
}

function SnapshotPanes({ data }: { data: ChatPayload }) {
  const { t } = useI18n();
  const snap = obj(data.snapshot);
  return (
    <div className="chat-kpis">
      <Kpi
        label={t("common.price")}
        value={snap && num(snap.price) !== null ? `${num(snap.price)?.toFixed(2)} ${str(snap.currency)}` : "—"}
        sub={snap ? str(snap.market) : undefined}
      />
      <Kpi
        label={t("common.buybox")}
        value={snap && num(snap.buybox) !== null ? num(snap.buybox)?.toFixed(2) : "—"}
      />
      <Kpi
        label={t("common.rank")}
        value={snap && snap.rank !== null && snap.rank !== undefined ? String(snap.rank) : "—"}
      />
    </div>
  );
}

function Payload({ intent, data }: { intent: string; data: ChatPayload }) {
  const { t, formatMoney, formatNumber } = useI18n();

  if (intent === "summary") {
    const total = obj(data.total) ?? {};
    const byMarket = arr(data.by_market) ?? [];
    const byStatus = arr(data.by_status) ?? [];
    const internalCurrency = str(byMarket[0]?.currency) || "USD";
    const columns: Column<ChatPayload>[] = [
      { header: t("common.market"), render: (r) => str(r.market) },
      { header: t("assistant.colOrders"), align: "right", render: (r) => formatNumber(num(r.orders), 0) },
      { header: t("assistant.colUnits"), align: "right", render: (r) => formatNumber(num(r.units), 0) },
      { header: t("assistant.colGross"), align: "right", render: (r) => formatMoney(num(r.gross), str(r.currency)) },
    ];
    return (
      <>
        <div className="chat-kpis">
          <Kpi label={t("assistant.colOrders")} value={formatNumber(num(total.orders), 0)} />
          <Kpi label={t("assistant.colUnits")} value={formatNumber(num(total.units), 0)} />
          <Kpi label={t("assistant.colGross")} value={formatMoney(num(total.gross), internalCurrency)} />
        </div>
        <DataTable columns={columns} rows={byMarket} rowKey={asinKey} emptyMessage={t("assistant.emptyData")} />
        {byStatus.length > 0 && (
          <div className="row wrap">
            <span className="muted">{t("assistant.colStatus")}:</span>
            {byStatus.map((s) => (
              <span key={str(s.status)} className="chip">
                {str(s.status)} · {formatNumber(num(s.count), 0)}
              </span>
            ))}
          </div>
        )}
      </>
    );
  }

  if (intent === "orders") {
    const total = obj(data.total) ?? {};
    const rows = arr(data.rows) ?? [];
    const columns: Column<ChatPayload>[] = [
      { header: t("assistant.colOrders"), render: (r) => <span className="mono">{str(r.amazon_order_id)}</span> },
      { header: t("assistant.colMarket"), render: (r) => str(r.market) },
      { header: t("assistant.colStatus"), render: (r) => str(r.status) },
      { header: t("assistant.colUnits"), align: "right", render: (r) => formatNumber(num(r.units), 0) },
      { header: t("assistant.colGross"), align: "right", render: (r) => formatMoney(num(r.gross), str(r.currency)) },
    ];
    return (
      <>
        <div className="chat-kpis">
          <Kpi label={t("assistant.colOrders")} value={formatNumber(num(total.orders), 0)} />
          <Kpi label={t("assistant.colUnits")} value={formatNumber(num(total.units), 0)} />
          <Kpi label={t("assistant.colGross")} value={formatMoney(num(total.gross), str(total.currency) || "USD")} />
        </div>
        <DataTable columns={columns} rows={rows} rowKey={asinKey} emptyMessage={t("assistant.emptyData")} />
      </>
    );
  }

  if (intent === "margin") {
    const rows = arr(data.rows) ?? [];
    const columns: Column<ChatPayload>[] = [
      { header: t("assistant.colAsin"), render: (r) => <span className="mono">{str(r.asin)}</span> },
      { header: t("assistant.colTitle"), render: (r) => str(r.title) || "—" },
      { header: t("assistant.colUnits"), align: "right", render: (r) => formatNumber(num(r.units), 0) },
      { header: t("assistant.colRevenue"), align: "right", render: (r) => formatMoney(num(r.revenue), str(r.currency)) },
      { header: t("assistant.colNet"), align: "right", render: (r) => formatMoney(num(r.net), str(r.currency)) },
      { header: t("assistant.colMargin"), align: "right", render: (r) => fmtPct(r.margin_pct) },
    ];
    return <DataTable columns={columns} rows={rows} rowKey={asinKey} emptyMessage={t("assistant.emptyData")} />;
  }

  if (intent === "inventory") {
    const rows = arr(data.rows) ?? [];
    const columns: Column<ChatPayload>[] = [
      { header: t("assistant.colAsin"), render: (r) => <span className="mono">{str(r.asin)}</span> },
      { header: t("assistant.colTitle"), render: (r) => str(r.title) || "—" },
      { header: t("assistant.colQty"), align: "right", render: (r) => formatNumber(num(r.quantity), 0) },
      { header: t("assistant.colPrice"), align: "right", render: (r) => (num(r.price) === null ? "—" : formatMoney(num(r.price), str(r.currency))) },
    ];
    return <DataTable columns={columns} rows={rows} rowKey={asinKey} emptyMessage={t("assistant.emptyData")} />;
  }

  if (intent === "alerts") {
    const rows = arr(data.rows) ?? [];
    const sevCls: Record<string, string> = { critical: "bad", warning: "warn", info: "good" };
    const columns: Column<ChatPayload>[] = [
      { header: t("assistant.colSeverity"), render: (r) => <span className={`chip ${sevCls[str(r.severity)] ?? ""}`}>{str(r.severity)}</span> },
      { header: t("assistant.colKind"), render: (r) => str(r.kind) },
      { header: t("assistant.colAsin"), render: (r) => (r.asin ? <span className="mono">{str(r.asin)}</span> : <span className="muted">—</span>) },
      { header: t("assistant.colMessage"), render: (r) => str(r.message) },
    ];
    return (
      <>
        <div className="chat-kpis">
          <Kpi label={t("alerts.open")} value={formatNumber(num(data.count), 0)} />
          <Kpi label={t("alerts.critical")} value={formatNumber(num(data.critical), 0)} />
        </div>
        <DataTable columns={columns} rows={rows} rowKey={asinKey} emptyMessage={t("assistant.emptyData")} />
      </>
    );
  }

  if (intent === "compare") {
    const rows = arr(data.rows) ?? [];
    const columns: Column<ChatPayload>[] = [
      { header: t("assistant.colAsin"), render: (r) => <span className="mono">{str(r.asin)}</span> },
      { header: t("assistant.colTitle"), render: (r) => str(r.title) || "—" },
      { header: t("assistant.colUs"), align: "right", render: (r) => (num(obj(r.us)?.price) === null ? "—" : formatMoney(num(obj(r.us)?.price), str(obj(r.us)?.currency) || "USD")) },
      { header: t("assistant.colCa"), align: "right", render: (r) => (num(obj(r.ca)?.price) === null ? "—" : formatMoney(num(obj(r.ca)?.price), str(obj(r.ca)?.currency) || "CAD")) },
      { header: t("assistant.colGap"), align: "right", render: (r) => (num(r.gap_usd) === null ? "—" : <TrendValue value={r.gap_usd} digits={2} />) },
    ];
    return <DataTable columns={columns} rows={rows} rowKey={asinKey} emptyMessage={t("assistant.emptyData")} />;
  }

  if (intent === "analyze") {
    const rows = arr(data.segments) ?? [];
    const columns: Column<ChatPayload>[] = [
      { header: t("assistant.colSegment"), render: (r) => str(r.name) || str(r.path) },
      { header: t("assistant.colProducts"), align: "right", render: (r) => formatNumber(num(r.products), 0) },
      { header: t("assistant.colUnits"), align: "right", render: (r) => formatNumber(num(r.units), 0) },
      { header: t("assistant.colRevenue"), align: "right", render: (r) => formatMoney(num(r.revenue), str(r.currency)) },
      { header: t("assistant.colNet"), align: "right", render: (r) => formatMoney(num(r.net), str(r.currency)) },
      { header: t("assistant.colMargin"), align: "right", render: (r) => fmtPct(r.margin_pct) },
    ];
    return <DataTable columns={columns} rows={rows} rowKey={asinKey} emptyMessage={t("assistant.emptyData")} />;
  }

  if (intent === "predict") {
    const insights = obj(data.insights) ?? {};
    const gainers = arr(insights.gainers) ?? [];
    const decliners = arr(insights.decliners) ?? [];
    const leaders = arr(insights.top_next_30d) ?? [];
    const bar = obj(data.chart);
    const emptyMsg = t("assistant.emptyData");
    return (
      <>
        {gainers.length > 0 && (
          <section className="card-inner">
            <h4 className="muted" style={{ margin: "0 0 8px", textTransform: "uppercase" }}>{t("assistant.gainers")}</h4>
            <PredictionRows rows={gainers} />
          </section>
        )}
        {decliners.length > 0 && (
          <section className="card-inner">
            <h4 className="muted" style={{ margin: "0 0 8px", textTransform: "uppercase" }}>{t("assistant.decliners")}</h4>
            <PredictionRows rows={decliners} />
          </section>
        )}
        {leaders.length > 0 && bar && (
          <section className="card-inner">
            <h4 className="muted" style={{ margin: "0 0 8px", textTransform: "uppercase" }}>{t("assistant.leaders")}</h4>
            <BarChart
              items={((bar.labels as string[]) ?? []).map((label, i) => ({
                label,
                value: num((bar.values as number[])?.[i]) ?? 0,
              }))}
              formatValue={(v) => v.toFixed(0)}
            />
          </section>
        )}
        {gainers.length === 0 && leaders.length === 0 && <div className="empty">{emptyMsg}</div>}
      </>
    );
  }

  if (intent === "asin") {
    const product = obj(data.product);
    if (!product) return <div className="empty">{t("assistant.emptyData")}</div>;
    return (
      <>
        <SnapshotPanes data={data} />
        <ForecastPane data={data} />
      </>
    );
  }

  return null;
}

const formatNum = (v: number | null | undefined, d = 1): string =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(d);

/* ------------------------------------------------------------- side panel */
function PrefsPanel({ prefs, onChange, onSaved }: {
  prefs: ChatPrefs;
  onChange: (p: ChatPrefs) => void;
  onSaved: (p: ChatPrefs) => void;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [flash, setFlash] = useState(false);

  const save = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await api.updateChatPrefs(prefs);
      onSaved(res.prefs);
      setFlash(true);
      window.setTimeout(() => setFlash(false), 1800);
    } catch (e) {
      setErr(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(false);
    }
  }, [prefs, onSaved]);

  const set = <K extends keyof ChatPrefs>(k: K, v: ChatPrefs[K]) => onChange({ ...prefs, [k]: v });

  return (
    <Card title={t("assistant.prefsTitle")} subtitle={t("assistant.prefsHint")}>
      <div className="form-stack">
        <label className="form-row">
          <span>{t("assistant.prefs.market")}</span>
          <select className="select" value={prefs.market} onChange={(e) => set("market", e.target.value)}>
            <option value="all">{t("assistant.prefs.market.all")}</option>
            <option value="US">US</option>
            <option value="CA">CA</option>
          </select>
        </label>
        <label className="form-row">
          <span>{t("assistant.prefs.language")}</span>
          <select className="select" value={prefs.language} onChange={(e) => set("language", e.target.value)}>
            <option value="en">English</option>
            <option value="fr">Français</option>
          </select>
        </label>
        <label className="form-row">
          <span>{t("assistant.prefs.currency")}</span>
          <select className="select" value={prefs.currency} onChange={(e) => set("currency", e.target.value)}>
            <option value="USD">USD</option>
            <option value="CAD">CAD</option>
          </select>
        </label>
        <label className="form-row">
          <span>{t("assistant.prefs.digest")}</span>
          <select className="select" value={prefs.digest} onChange={(e) => set("digest", e.target.value)}>
            <option value="daily">{t("assistant.prefs.digest.daily")}</option>
            <option value="weekly">{t("assistant.prefs.digest.weekly")}</option>
            <option value="off">{t("assistant.prefs.digest.off")}</option>
          </select>
        </label>
        <div className="row">
          <button className="btn-ghost" disabled={busy} onClick={() => void save()}>
            {busy ? t("common.loading") : t("common.save")}
          </button>
          {flash && <span className="chip good">{t("assistant.prefs.saved")}</span>}
        </div>
        {err && <ErrorBox error={err} onRetry={() => void save()} />}
      </div>
    </Card>
  );
}



function PredictionsCard() {
  const { t, formatDate } = useI18n();
  const [state, setState] = useState<{ data: import("../api/types").ChatPredictionsPage | null; loading: boolean; error: Error | null }>({
    data: null,
    loading: true,
    error: null,
  });
  const load = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }));
    api
      .chatPredictions(6)
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((e: unknown) => setState({ data: null, loading: false, error: e instanceof Error ? e : new Error(String(e)) }));
  }, []);

  useEffect(load, [load]);

  const ins = state.data?.insights ?? null;

  return (
    <Card
      title={t("assistant.insightsTitle")}
      subtitle={t("assistant.insightsHint")}
      actions={
        <div className="row">
          <button className="btn-ghost" onClick={load} title={t("common.refresh")}>
            <Icon name="refresh" size={15} />
          </button>
          {ins?.generated_at && <span className="chip">{formatDate(ins.generated_at)}</span>}
        </div>
      }
    >
      {state.error ? (
        <ErrorBox error={state.error} onRetry={load} />
      ) : state.loading && !state.data ? (
        <div className="loading-row">{t("common.loading")}</div>
      ) : ins && ins.count > 0 ? (
        <div className="form-stack">
          {ins.gainers.length > 0 && (
            <div>
              <h4 style={{ margin: "0 0 6px", fontSize: 12, textTransform: "uppercase", color: "var(--muted)" }}>
                {t("assistant.gainers")}
              </h4>
              <PredictionRows rows={ins.gainers.slice(0, 3)} />
            </div>
          )}
          {ins.decliners.length > 0 && (
            <div>
              <h4 style={{ margin: 0, fontSize: 12, textTransform: "uppercase", color: "var(--muted)" }}>
                {t("assistant.decliners")}
              </h4>
              <PredictionRows rows={ins.decliners.slice(0, 3)} />
            </div>
          )}
        </div>
      ) : (
        <div className="empty">{t("forecast.empty")}</div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------- page */
export default function AssistantPage() {
  const { t } = useI18n();
  const [items, setItems] = useState<Exchange[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [prefs, setPrefs] = useState<ChatPrefs>(PREF_DEFAULTS);
  const sessionIdRef = useRef<string | null>(null);
  const idRef = useRef(0);
  const logRef = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);

  const greeting = useCallback(
    (): Exchange => ({ id: idRef.current++, role: "assistant", intent: "greeting", text: t("assistant.greeting"), data: null, createdAt: null }),
    [t]
  );

  useEffect(() => {
    let cancelled = false;
    const sid = (() => {
      try {
        return localStorage.getItem(SESSION_KEY);
      } catch {
        return null;
      }
    })();
    sessionIdRef.current = sid;
    api
      .chatPrefs()
      .then((page) => {
        if (!cancelled) setPrefs(page.prefs);
      })
      .catch(() => {
        /* keep defaults */
      });
    if (sid) {
      api
        .chatMessages(sid, 50)
        .then((page) => {
          if (cancelled) return;
          setItems(
            page.messages.map((m: ChatMessage): Exchange => ({
              id: idRef.current++,
              role: m.role,
              intent: m.intent || "",
              text: m.text || "",
              data: m.data,
              createdAt: m.created_at,
            }))
          );
        })
        .catch(() => {
          if (cancelled) return;
          try {
            localStorage.removeItem(SESSION_KEY);
          } catch {
            /* ignore */
          }
          sessionIdRef.current = null;
          setItems([greeting()]);
        })
        .finally(() => {
          if (!cancelled) setReady(true);
        });
    } else {
      sessionIdRef.current = null;
      setItems([greeting()]);
      setReady(true);
    }
    return () => {
      cancelled = true;
    };
  }, [greeting]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [items, busy, ready]);

  const send = useCallback(
    async (message: string) => {
      const q = message.trim();
      if (!q || busy) return;
      setInput("");
      setItems((prev) => [...prev, { id: idRef.current++, role: "user", intent: "", text: q, data: null, createdAt: null }]);
      setBusy(true);
      try {
        const res = await api.chatSend(q, {
          session_id: sessionIdRef.current || undefined,
          lang: prefs.language,
        });
        sessionIdRef.current = res.session_id;
        try {
          localStorage.setItem(SESSION_KEY, res.session_id);
        } catch {
          /* ignore */
        }
        setPrefs(res.prefs);
        setItems((prev) => [
          ...prev,
          { id: idRef.current++, role: "assistant", intent: res.reply.intent, text: res.reply.text, data: res.reply.data, createdAt: res.reply.created_at },
        ]);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setItems((prev) => [
          ...prev,
          { id: idRef.current++, role: "assistant", intent: "error", text: msg, data: null, createdAt: null },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [busy, prefs.language]
  );

  const reset = useCallback(async () => {
    if (sessionIdRef.current) {
      try {
        await api.chatDeleteSession(sessionIdRef.current);
      } catch {
        /* stale session is fine */
      }
    }
    sessionIdRef.current = null;
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch {
      /* ignore */
    }
    setItems([greeting()]);
  }, [greeting]);

  const chips = (() => {
    const lang = prefs.language === "fr" ? "fr" : "en";
    return QUICK.map((q) => ({ label: t(q.key), prompt: q[lang] }));
  })();

  return (
    <div className="assistant-grid">
      <Card
        title={t("assistant.title")}
        subtitle={t("assistant.subtitle")}
        actions={
          <div className="row">
            <button className="btn-ghost" onClick={() => void reset()}>{t("assistant.clear")}</button>
          </div>
        }
      >
        <div className="chat-card">
          {ready ? (
            <div className="chat-log" ref={logRef}>
              {items.map((m) => (
                <div key={m.id} className={`chat-msg ${m.role}`}>
                  {m.role === "user" ? (
                    <div className="chat-bubble user">{m.text}</div>
                  ) : (
                    <div className="chat-bubble ai">
                      <div className="chat-meta">
                        {m.intent !== "greeting" && m.intent !== "error" && (
                          <span className="chip">{t("assistant.intent", { intent: m.intent })}</span>
                        )}
                      </div>
                      <div className="chat-text">{m.text}</div>
                      {m.data && <div className="chat-data"><Payload intent={m.intent} data={m.data} /></div>}
                    </div>
                  )}
                </div>
              ))}
              {busy && (
                <div className="chat-msg ai">
                  <div className="chat-bubble ai"><span className="chip">…</span></div>
                </div>
              )}
            </div>
          ) : (
            <div className="loading-row">{t("assistant.loadingHistory")}</div>
          )}

          <div className="quick-row">
            <span className="muted">{t("common.quick")}:</span>
            {chips.map((c) => (
              <button key={c.label} className="btn-ghost" disabled={busy} onClick={() => void send(c.prompt)}>
                {c.label}
              </button>
            ))}
          </div>

          <form
            className="chat-input"
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
          >
            <input
              className="input"
              autoFocus
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t("assistant.placeholder")}
              disabled={busy}
            />
            <button className="btn-ghost" type="submit" disabled={busy || !input.trim()}>
              <Icon name="send" size={15} />
            </button>
          </form>
        </div>
      </Card>

      <aside className="assistant-side">
        <PrefsPanel
          prefs={prefs}
          onChange={setPrefs}
          onSaved={setPrefs}
        />
        <PredictionsCard />
      </aside>
    </div>
  );
}