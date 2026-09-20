import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Card, DataTable, ErrorBox, Loading, MarketFilter, Pagination, type Column,
} from "../components/ui";
import { useApi, useDebounced } from "../hooks";
import { useI18n } from "../i18n";
import type { OrderRow } from "../api/types";

const LIMIT = 25;

export default function OrdersPage() {
  const { t, tf, formatDate, formatInt, formatMoney } = useI18n();
  const [market, setMarket] = useState("");
  const [q, setQ] = useState("");
  const debouncedQ = useDebounced(q);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [market, debouncedQ]);

  const markets = useApi(api.marketplaces, []);
  const orders = useApi(
    () => api.orders({ market: market || undefined, q: debouncedQ || undefined, limit: LIMIT, offset }),
    [market, debouncedQ, offset]
  );

  const columns: Column<OrderRow>[] = [
    { header: t("orders.amazonId"), render: (r) => <span className="mono">{r.amazon_order_id}</span> },
    { header: t("common.market"), render: (r) => <strong>{r.market}</strong> },
    { header: t("common.status"), render: (r) => <span className="chip">{tf("status", r.status)}</span> },
    { header: t("common.date"), render: (r) => formatDate(r.purchase_date, true) },
    { header: t("common.buyer"), render: (r) => r.buyer_name || "—" },
    {
      header: t("common.location"),
      render: (r) => [r.city, r.state_or_region].filter(Boolean).join(", ") || "—",
    },
    { header: t("common.units"), align: "right", render: (r) => formatInt(r.units) },
    { header: t("common.gross"), align: "right", render: (r) => formatMoney(r.gross) },
  ];

  return (
    <Card
      title={t("orders.title")}
      subtitle={t("orders.subtitle")}
      actions={
        <button className="btn-ghost" onClick={orders.reload}>{t("common.refresh")}</button>
      }
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

      {orders.error ? (
        <ErrorBox error={orders.error} onRetry={orders.reload} />
      ) : orders.loading && !orders.data ? (
        <Loading />
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={orders.data?.orders ?? []}
            rowKey={(r) => r.id}
            emptyMessage={t("common.noResults")}
          />
          <Pagination
            offset={offset}
            limit={LIMIT}
            total={orders.data?.total ?? 0}
            onChange={setOffset}
          />
        </>
      )}
    </Card>
  );
}
