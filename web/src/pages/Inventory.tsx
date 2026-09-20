import { useState } from "react";
import { api } from "../api/client";
import { Card, DataTable, ErrorBox, Loading, MarketFilter, type Column } from "../components/ui";
import { useApi, useDebounced } from "../hooks";
import { useI18n } from "../i18n";
import type { InventoryRow } from "../api/types";

export default function InventoryPage() {
  const { t, formatDate, formatInt, formatMoney } = useI18n();
  const [market, setMarket] = useState("");
  const [q, setQ] = useState("");
  const debouncedQ = useDebounced(q);

  const markets = useApi(api.marketplaces, []);
  const inventory = useApi(
    () => api.inventory({ market: market || undefined, q: debouncedQ || undefined, limit: 200 }),
    [market, debouncedQ]
  );

  const columns: Column<InventoryRow>[] = [
    { header: t("common.asin"), render: (r) => <span className="mono">{r.asin}</span> },
    { header: t("common.title"), render: (r) => r.title || "—" },
    { header: t("common.brand"), render: (r) => r.brand || "—" },
    { header: t("common.market"), render: (r) => <strong>{r.market}</strong> },
    {
      header: t("inventory.listedPrice"),
      align: "right",
      render: (r) => formatMoney(r.price, r.currency),
    },
    {
      header: t("common.quantity"),
      align: "right",
      render: (r) => (
        <span className={r.quantity <= 0 ? "neg" : undefined}>{formatInt(r.quantity)}</span>
      ),
    },
    { header: t("common.captured"), render: (r) => formatDate(r.captured_at, true) },
  ];

  return (
    <Card
      title={t("inventory.title")}
      subtitle={t("inventory.subtitle")}
      actions={<button className="btn-ghost" onClick={inventory.reload}>{t("common.refresh")}</button>}
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

      {inventory.error ? (
        <ErrorBox error={inventory.error} onRetry={inventory.reload} />
      ) : inventory.loading && !inventory.data ? (
        <Loading />
      ) : (
        <DataTable
          columns={columns}
          rows={inventory.data?.rows ?? []}
          rowKey={(r) => `${r.market}-${r.asin}`}
          emptyMessage={t("common.noResults")}
        />
      )}
    </Card>
  );
}
