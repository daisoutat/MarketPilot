/* Shared, dependency-free UI primitives. */
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import { useI18n } from "../i18n";

/* ------------------------------------------------------------------ icons */
export type IconName =
  | "overview" | "orders" | "inventory" | "margin" | "research" | "compare" | "sync" | "alerts"
  | "categories" | "forecast" | "assistant" | "send"
  | "sun" | "moon" | "globe" | "refresh";

const PATHS: Record<IconName, string> = {
  overview: "M3 12h4l3 8 4-16 3 8h4",
  orders: "M6 2h9l3 3v15H6zM9 8h6M9 12h6M9 16h4",
  inventory: "M3 7l9-4 9 4-9 4-9-4zm0 0v10l9 4 9-4V7",
  margin: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  research: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm5 12l5 5",
  compare: "M4 7h7v10H4zM13 7h7v10h-7zM7 4v3M17 17v3",
  sync: "M21 12a9 9 0 1 1-3-6.7M21 4v5h-5",
  alerts: "M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
  categories: "M3 5h8v8H3zM13 3h8v6h-8zM13 11h8v10h-8zM3 15h8v6H3z",
  forecast: "M3 20V12M9 20V5M15 20v-8M21 20H2M3 20l6-9 6 3 6-8",
  assistant: "M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-4 4v-4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  send: "M3 20L21 12 3 4v6l12 2-12 2z",
  sun: "M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7l1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2l-1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.6 3.8 5.7 3.8 9S14.5 18.4 12 21c-2.5-2.6-3.8-5.7-3.8-9S9.5 5.6 12 3z",
  refresh: "M20 11a8 8 0 1 0-2.3 6.3M20 4v7h-7",
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false">
      <path d={PATHS[name]} />
    </svg>
  );
}

/* --------------------------------------------------------------- feedback */
export function Spinner() {
  return <span className="spinner" role="status" aria-label="loading" />;
}

export function Loading() {
  const { t } = useI18n();
  return (
    <div className="loading-row">
      <Spinner /> {t("common.loading")}
    </div>
  );
}

export function Empty({ message }: { message?: string }) {
  const { t } = useI18n();
  return <div className="empty">{message ?? t("common.empty")}</div>;
}

export function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const { t } = useI18n();
  const isNetwork = error instanceof ApiError && error.status === 0;
  const message = isNetwork
    ? t("error.network")
    : error instanceof ApiError
      ? t("error.http", { status: error.status })
      : error.message;
  return (
    <div className="errorbox" role="alert">
      <strong>{t("error.title")}</strong>
      <span className="muted">{message}</span>
      <span className="spacer" />
      {onRetry && (
        <button className="btn-ghost" onClick={onRetry}>
          {t("common.retry")}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ cards */
export function Card({
  title, subtitle, actions, children,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {subtitle && <span className="card-sub">{subtitle}</span>}
          <span className="spacer" />
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub !== undefined && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------- tables */
export interface Column<T> {
  header: string;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
}

export function DataTable<T>({
  columns, rows, rowKey, emptyMessage,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  emptyMessage?: string;
}) {
  if (!rows.length) return <Empty message={emptyMessage} />;
  return (
    <div className="table-wrap table-scroll">
      <table className="data">
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th key={i} className={c.align === "right" ? "num" : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c, i) => (
                <td key={i} className={c.align === "right" ? "num" : undefined}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({
  offset, limit, total, onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  const { t } = useI18n();
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="pager">
      <span>{t("orders.showing", { from, to, total })}</span>
      <button className="btn-ghost" disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        {t("common.prev")}
      </button>
      <span>{t("common.page")} {page} {t("common.of")} {pages}</span>
      <button className="btn-ghost" disabled={to >= total} onClick={() => onChange(offset + limit)}>
        {t("common.next")}
      </button>
    </div>
  );
}

/* ---------------------------------------------------------------- filters */
export function MarketFilter({
  value, markets, onChange,
}: {
  value: string;
  markets: string[];
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  return (
    <label className="row">
      <span className="muted">{t("common.market")}</span>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t("common.allMarkets")}</option>
        {markets.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>
    </label>
  );
}

export function StatusChip({ status }: { status: string }) {
  const { tf } = useI18n();
  const cls = status === "done" || status === "active" ? "good" : status === "error" ? "bad" : "warn";
  return <span className={`chip ${cls}`}>{tf("sync.status", status)}</span>;
}

export function LanguageToggle({ lang, onChange }: { lang: "en" | "fr"; onChange: (l: "en" | "fr") => void }) {
  return (
    <div className="lang-switch" role="group" aria-label="Language">
      <button className={lang === "en" ? "active" : ""} onClick={() => onChange("en")}>EN</button>
      <button className={lang === "fr" ? "active" : ""} onClick={() => onChange("fr")}>FR</button>
    </div>
  );
}

export function ThemeToggle({ theme, onToggle }: { theme: "dark" | "light"; onToggle: () => void }) {
  const { t } = useI18n();
  const label = theme === "dark" ? t("common.themeLight") : t("common.themeDark");
  return (
    <button className="btn-ghost row" onClick={onToggle} title={label}>
      <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
      <span>{label}</span>
    </button>
  );
}
