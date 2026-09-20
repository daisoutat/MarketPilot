import { useState } from "react";
import { api } from "../api/client";
import { Card, DataTable, ErrorBox, Loading, MarketFilter, type Column } from "../components/ui";
import { useApi, useDebounced } from "../hooks";
import { useI18n } from "../i18n";
import type { MarginRow } from "../api/types";

const WINDOWS = [30, 90, 180, 365];

export default function MarginPage() {
  const { t, formatInt, formatMoney, formatPercent } = useI18n();
  const [days, setDays] = useState(90);
  const [market, setMarket] = useState("");
  const [minUnits, setMinUnits] = useState(0);
  const [q, setQ] = useState("");
  const debouncedQ = useDebounced(q);

  const markets = useApi(api.marketplaces, []);
  const margin = useApi(
    () =>
      api.margin({
        days,
        market: market || undefined,
        min_units: minUnits || undefined,
        q: debouncedQ || undefined,
        limit: 100,
      }),
    [days, market, minUnits, debouncedQ]
  );

  const columns: Column<MarginRow>[] = [
    { header: t("common.asin"), render: (r) => <span className="mono">{r.asin}</span> },
    { header: t("common.title"), render: (r) => r.title || "—" },
    { header: t("common.market"), render: (r) => <strong>{r.market}</strong> },
    { header: t("common.units"), align: "right", render: (r) => formatInt(r.units) },
    { header: t("common.revenue"), align: "right", render: (r) => formatMoney(r.revenue, r.currency) },
    { header: t("common.fees"), align: "right", render: (r) => formatMoney(r.fees, r.currency) },
    {
      header: t("common.net"),
      align: "right",
      render: (r) => (
        <span className={r.net >= 0 ? "pos" : "neg"}>{formatMoney(r.net, r.currency)}</span>
      ),
    },
    {
      header: t("margin.marginPct"),
      align: "right",
      render: (r) => (
        <span className={r.margin_pct !== null && r.margin_pct < 0 ? "neg" : undefined}>
          {formatPercent(r.margin_pct)}
        </span>
      ),
    },
    { header: t("common.avgPrice"), align: "right", render: (r) => formatMoney(r.avg_price, r.currency) },
    { header: t("common.currency"), render: (r) => r.currency },
  ];

  return (
    <Card
      title={t("margin.title")}
      subtitle={t("margin.subtitle")}
      actions={<button className="btn-ghost" onClick={margin.reload}>{t("common.refresh")}</button>}
    >
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <label className="row">
          <span className="muted">{t("common.window")}</span>
          <select className="select" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {WINDOWS.map((d) => (
              <option key={d} value={d}>{`${d} ${t("common.days")}`}</option>
            ))}
          </select>
        </label>
        <MarketFilter
          value={market}
          markets={(markets.data?.marketplaces ?? []).map((m) => m.code)}
          onChange={setMarket}
        />
        <label className="row">
          <span className="muted">{t("common.minUnits")}</span>
          <input
            className="input"
            type="number"
            min={0}
            value={minUnits}
            onChange={(e) => setMinUnits(Math.max(0, Number(e.target.value) || 0))}
            style={{ width: 90 }}
          />
        </label>
        <input
          className="input"
          placeholder={t("common.searchPlaceholder")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 200 }}
        />
        <span className="spacer" />
      </div>

      {margin.error ? (
        <ErrorBox error={margin.error} onRetry={margin.reload} />
      ) : margin.loading && !margin.data ? (
        <Loading />
      ) : (
        <DataTable
          columns={columns}
          rows={margin.data?.rows ?? []}
          rowKey={(r) => `${r.market}-${r.asin}`}
          emptyMessage={t("common.noResults")}
        />
      )}
    </Card>
  );
}
