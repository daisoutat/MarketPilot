import { useState } from "react";
import { api } from "../api/client";
import { BarChart, LineChart } from "../components/charts";
import { Card, DataTable, ErrorBox, Kpi, Loading, type Column } from "../components/ui";
import { useApi } from "../hooks";
import { useI18n } from "../i18n";
import type { StatusCount, SummaryMarket } from "../api/types";

const WINDOWS = [7, 30, 90, 365];

export default function OverviewPage() {
  const { t, tf, formatInt, formatMoney } = useI18n();
  const [days, setDays] = useState(30);

  const summary = useApi(() => api.summary(days), [days]);
  const series = useApi(() => api.series(days), [days]);

  const byMarketColumns: Column<SummaryMarket>[] = [
    { header: t("common.market"), render: (r) => <strong>{r.market}</strong> },
    { header: t("common.orders"), align: "right", render: (r) => formatInt(r.orders) },
    { header: t("common.units"), align: "right", render: (r) => formatInt(r.units) },
    { header: t("common.gross"), align: "right", render: (r) => formatMoney(r.gross) },
  ];

  const statusColumns: Column<StatusCount>[] = [
    { header: t("common.status"), render: (r) => tf("status", r.status) },
    { header: t("common.total"), align: "right", render: (r) => formatInt(r.count) },
  ];

  return (
    <>
      <div className="toolbar">
        <label className="row">
          <span className="muted">{t("common.window")}</span>
          <select className="select" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {WINDOWS.map((d) => (
              <option key={d} value={d}>{`${d} ${t("common.days")}`}</option>
            ))}
          </select>
        </label>
        <span className="spacer" />
        <button className="btn-ghost" onClick={() => { summary.reload(); series.reload(); }}>
          {t("common.refresh")}
        </button>
      </div>

      {summary.error && <ErrorBox error={summary.error} onRetry={summary.reload} />}

      {summary.data && (
        <div className="kpi-grid">
          <Kpi label={t("overview.kpi.orders")} value={formatInt(summary.data.total.orders)} />
          <Kpi label={t("overview.kpi.units")} value={formatInt(summary.data.total.units)} />
          <Kpi label={t("overview.kpi.gross")} value={formatMoney(summary.data.total.gross)} />
          <Kpi label={t("overview.kpi.markets")} value={formatInt(summary.data.by_market.length)} />
        </div>
      )}
      {!summary.data && summary.loading && <Loading />}

      <div className="grid-2">
        <Card title={t("overview.series")} subtitle={t("overview.series.caption")}>
          {series.loading && !series.data ? (
            <Loading />
          ) : series.error ? (
            <ErrorBox error={series.error} onRetry={series.reload} />
          ) : (
            <LineChart
              points={(series.data?.points ?? []).map((p) => ({
                label: p.date.length >= 10 ? p.date.slice(5) : p.date,
                value: p.orders,
              }))}
              formatValue={(v) => formatInt(v)}
            />
          )}
        </Card>

        <Card title={t("overview.byStatus")}>
          <DataTable
            columns={statusColumns}
            rows={summary.data?.by_status ?? []}
            rowKey={(r) => r.status}
          />
        </Card>
      </div>

      <Card
        title={t("overview.byMarket")}
        actions={
          <button className="btn-ghost" onClick={summary.reload}>{t("common.refresh")}</button>
        }
      >
        <BarChart
          items={(summary.data?.by_market ?? []).map((m) => ({ label: m.market, value: m.gross }))}
          formatValue={(v) => formatMoney(v)}
        />
        <div style={{ marginTop: 14 }}>
          <DataTable
            columns={byMarketColumns}
            rows={summary.data?.by_market ?? []}
            rowKey={(r) => r.market}
          />
        </div>
      </Card>
    </>
  );
}
