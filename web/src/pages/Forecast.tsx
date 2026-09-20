import { useState } from "react";
import { api } from "../api/client";
import { Card, DataTable, Empty, ErrorBox, Loading, MarketFilter, type Column } from "../components/ui";
import { ForecastChart } from "../components/charts";
import { useApi } from "../hooks";
import { useI18n } from "../i18n";
import type { ForecastDetail, ForecastRow } from "../api/types";

const _fmt = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);

function Explain({ detail }: { detail: ForecastDetail }) {
  const { t, formatNumber } = useI18n();
  const params = detail.params;
  if (!params) return <div className="muted">{t("forecast.empty")}</div>;
  const trend = params.trend_per_week_pct;
  const seasonal = params.seasonal ?? {};
  const peak = Object.entries(seasonal).reduce<[string, number] | null>(
    (best, [day, v]) => (best === null || v > best[1] ? [day, v] : best),
    null
  );
  return (
    <div className="row wrap">
      <span className="muted">
        {trend === null ? (
          t("forecast.explainFlat")
        ) : (
          t("forecast.explainTrend", {
            direction: trend >= 0 ? "+" : "−",
            weekly: `${formatNumber(Math.abs(trend), 1)} %`,
          })
        )}
      </span>
      {peak && peak[1] > 0 && (
        <span className="muted">· {t("forecast.explainPeak", { day: t(`forecast.seasonal.${peak[0]}` as never) })}</span>
      )}
    </div>
  );
}

function Detail({ productId }: { productId: number }) {
  const { t, formatDate } = useI18n();
  const fc = useApi(() => api.forecastDetail(productId), [productId]);
  if (fc.error) return <ErrorBox error={fc.error} onRetry={fc.reload} />;
  if (fc.loading && !fc.data) return <Loading />;
  const d = fc.data;
  if (!d) return <Empty />;

  const series = [
    ...(d.history?.dates ?? []).map((label, i) => ({
      label: label.slice(5),
      actual: d.history?.values?.[i] ?? null,
    })),
    ...(d.points?.dates ?? []).map((label, i) => ({
      label: label.slice(5),
      forecast: d.points?.yhat?.[i] ?? null,
      lo: d.points?.lo?.[i] ?? null,
      hi: d.points?.hi?.[i] ?? null,
    })),
  ];
  const unit = d.unit || "units/day";
  const show = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${_fmt(v, 1)} ${unit}`);

  return (
    <Card
      title={d.title || d.asin}
      subtitle={`${d.asin} · ${d.brand} · ${t("forecast.detail")}`}
      actions={<span className="chip">{t("common.date")}: {formatDate(d.generated_at)}</span>}
    >
      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))" }}>
        <span className="kpi"><span className="kpi-label">{t("forecast.mean")}</span><span className="kpi-value">{show(d.params?.mean_per_day)}</span></span>
        <span className="kpi"><span className="kpi-label">{t("forecast.trend")}</span><span className="kpi-value">{d.params?.trend_per_week_pct == null ? "—" : `${_fmt(d.params.trend_per_week_pct, 1)} %`}</span></span>
        <span className="kpi"><span className="kpi-label">{t("forecast.volatility")}</span><span className="kpi-value">{show(d.params?.volatility)}</span></span>
        <span className="kpi"><span className="kpi-label">{t("forecast.rmse")}</span><span className="kpi-value">{show(d.params?.rmse)}</span></span>
        <span className="kpi"><span className="kpi-label">{t("forecast.horizon")}</span><span className="kpi-value">{d.horizon} {t("common.days").toLowerCase()}</span></span>
      </div>
      <div className="row" style={{ margin: "10px 0 4px", fontSize: 13 }}>
        <span className="muted">▬ {t("forecast.history")}</span>
        <span>▬ {t("forecast.outlook")}</span>
        <span className="muted">{t("forecast.band")}</span>
      </div>
      <ForecastChart series={series} formatValue={(v) => _fmt(v, 1)} />
      <div style={{ marginTop: 10 }}><Explain detail={d} /></div>
    </Card>
  );
}

export default function ForecastPage() {
  const { t, formatNumber } = useI18n();
  const [market, setMarket] = useState("");
  const [limit, setLimit] = useState(50);
  const [selected, setSelected] = useState<number | null>(null);

  const markets = useApi(api.marketplaces, []);
  const list = useApi(
    () => api.forecasts({ days: 90, market: market || undefined, limit }),
    [market, limit]
  );

  const columns: Column<ForecastRow>[] = [
    {
      header: t("common.title"),
      render: (r) => (
        <button className="btn-ghost" onClick={() => setSelected(selected === r.product_id ? null : r.product_id)}>
          {r.title || r.asin}
        </button>
      ),
    },
    { header: t("common.asin"), render: (r) => <span className="mono">{r.asin}</span> },
    { header: t("forecast.mean"), align: "right", render: (r) => formatNumber(r.mean_per_day, 1) },
    { header: t("forecast.trend"), align: "right", render: (r) => (r.trend_per_week_pct == null ? "—" : `${_fmt(r.trend_per_week_pct, 1)} %`) },
    { header: t("forecast.volatility"), align: "right", render: (r) => (r.volatility == null ? "—" : _fmt(r.volatility, 1)) },
    { header: t("forecast.rmse"), align: "right", render: (r) => (r.rmse == null ? "—" : _fmt(r.rmse, 1)) },
    { header: t("forecast.nextTotal", { n: 30 }), align: "right", render: (r) => formatNumber(r.horizon_total, 1) },
    { header: t("common.window"), align: "right", render: (r) => `${r.window_days}` },
  ];

  return (
    <>
      <Card
        title={t("forecast.title")}
        subtitle={t("forecast.subtitle")}
        actions={<button className="btn-ghost" onClick={list.reload}>{t("common.refresh")}</button>}
      >
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <MarketFilter
            value={market}
            markets={(markets.data?.marketplaces ?? []).map((m) => m.code)}
            onChange={setMarket}
          />
          <label className="row">
            <span className="muted">Limit</span>
            <select className="select" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {[25, 50, 100, 200].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
        </div>

        {list.error ? (
          <ErrorBox error={list.error} onRetry={list.reload} />
        ) : list.loading && !list.data ? (
          <Loading />
        ) : (
          <DataTable
            columns={columns}
            rows={list.data?.rows ?? []}
            rowKey={(r) => r.product_id}
            emptyMessage={t("forecast.empty")}
          />
        )}
      </Card>

      {selected !== null && <Detail productId={selected} />}
    </>
  );
}