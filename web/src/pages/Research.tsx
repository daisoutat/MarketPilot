import { useState } from "react";
import { api } from "../api/client";
import { Card, DataTable, ErrorBox, Loading, MarketFilter, type Column } from "../components/ui";
import { useApi, useDebounced } from "../hooks";
import { useI18n } from "../i18n";
import type { ResearchRow } from "../api/types";

export default function ResearchPage() {
  const { t, formatDate, formatInt, formatMoney } = useI18n();
  const [market, setMarket] = useState("");
  const [q, setQ] = useState("");
  const debouncedQ = useDebounced(q);

  const markets = useApi(api.marketplaces, []);
  const research = useApi(
    () => api.research({ market: market || undefined, q: debouncedQ || undefined, limit: 200 }),
    [market, debouncedQ]
  );

  const columns: Column<ResearchRow>[] = [
    { header: t("common.asin"), render: (r) => <span className="mono">{r.asin}</span> },
    { header: t("common.title"), render: (r) => r.title || "—" },
    { header: t("common.market"), render: (r) => <strong>{r.market}</strong> },
    { header: t("common.keywords"), render: (r) => r.keywords || "—" },
    { header: t("common.price"), align: "right", render: (r) => formatMoney(r.price, r.currency) },
    {
      header: t("common.priceUsd"),
      align: "right",
      render: (r) => <strong>{formatMoney(r.price_usd, "USD")}</strong>,
    },
    {
      header: t("common.buybox"),
      align: "right",
      render: (r) => (r.buybox === null ? "—" : formatMoney(r.buybox, r.currency)),
    },
    { header: t("common.rank"), align: "right", render: (r) => (r.sales_rank === null ? "—" : formatInt(r.sales_rank)) },
    { header: t("common.lastSnap"), render: (r) => formatDate(r.last_snap_at ?? r.captured_at, true) },
  ];

  return (
    <Card
      title={t("research.title")}
      subtitle={t("research.subtitle")}
      actions={<button className="btn-ghost" onClick={research.reload}>{t("common.refresh")}</button>}
    >
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <MarketFilter
          value={market}
          markets={(markets.data?.marketplaces ?? []).map((m) => m.code)}
          onChange={setMarket}
        />
        <input
          className="input"
          placeholder={t("common.searchPlaceholder")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 220 }}
        />
      </div>

      {research.error ? (
        <ErrorBox error={research.error} onRetry={research.reload} />
      ) : research.loading && !research.data ? (
        <Loading />
      ) : (
        <DataTable
          columns={columns}
          rows={research.data?.rows ?? []}
          rowKey={(r) => `${r.market}-${r.asin}`}
          emptyMessage={t("common.noResults")}
        />
      )}
    </Card>
  );
}
