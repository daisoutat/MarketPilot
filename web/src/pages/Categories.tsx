import { useState } from "react";
import { api } from "../api/client";
import { Card, DataTable, ErrorBox, Loading, MarketFilter, type Column } from "../components/ui";
import { useApi } from "../hooks";
import { useI18n } from "../i18n";
import type { CategoryNode, SegmentProduct } from "../api/types";

const DAYS = [30, 90, 180, 365];

const KIND_CLS: Record<string, string> = { category: "good", niche: "warn", family: "" };

function KindChip({ kind }: { kind: string }) {
  return <span className={`chip ${KIND_CLS[kind] ?? ""}`}>{kind}</span>;
}

function FamilyDrill({ family, days, market }: { family: CategoryNode; days: number; market: string }) {
  const { t, formatMoney } = useI18n();
  const seg = useApi(() => api.segment(family.id, { days, market: market || undefined }), [family.id, days, market]);

  const columns: Column<SegmentProduct>[] = [
    { header: t("common.asin"), render: (r) => <span className="mono">{r.asin}</span> },
    { header: t("common.title"), render: (r) => r.title || "—" },
    { header: t("common.units"), align: "right", render: (r) => r.units },
    { header: t("common.revenue"), align: "right", render: (r) => formatMoney(r.revenue, r.currency) },
    { header: t("common.net"), align: "right", render: (r) => formatMoney(r.net, r.currency) },
    { header: t("common.margin"), align: "right", render: (r) => (r.margin_pct === null ? "—" : `${r.margin_pct} %`) },
    { header: t("common.rank"), align: "right", render: (r) => r.rank ?? "—" },
    { header: t("common.price"), align: "right", render: (r) => formatMoney(r.price, r.currency) },
  ];

  return (
    <div style={{ padding: "10px 14px 14px 34px" }}>
      <div className="muted" style={{ marginBottom: 8 }}>
        {family.path} — {t("categories.drill")}
      </div>
      {seg.error ? (
        <ErrorBox error={seg.error} onRetry={seg.reload} />
      ) : seg.loading && !seg.data ? (
        <Loading />
      ) : (
        <DataTable columns={columns} rows={seg.data?.rows ?? []} rowKey={(r) => r.product_id} />
      )}
    </div>
  );
}

export default function CategoriesPage() {
  const { t, formatMoney, formatInt } = useI18n();
  const [days, setDays] = useState(90);
  const [market, setMarket] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  const markets = useApi(api.marketplaces, []);
  const tree = useApi(() => api.tree({ days, market: market || undefined }), [days, market]);

  const columns: Column<CategoryNode>[] = [
    {
      header: t("categories.level"),
      render: (r) => (
        <button
          className="btn-ghost"
          style={{
            marginLeft: r.depth * 20,
            fontWeight: r.kind === "family" ? 600 : 500,
          }}
          disabled={r.kind !== "family"}
          onClick={() => setOpen(open === r.id ? null : r.id)}
        >
          {r.kind === "family" ? (open === r.id ? "▾ " : "▸ ") : ""}
          {r.name}
        </button>
      ),
    },
    { header: "", render: (r) => <KindChip kind={r.kind} /> },
    { header: t("categories.products"), align: "right", render: (r) => formatInt(r.product_count || null) },
    { header: t("common.units"), align: "right", render: (r) => formatInt(r.units || null) },
    { header: t("common.revenue"), align: "right", render: (r) => formatMoney(r.revenue, r.currency) },
    { header: t("common.net"), align: "right", render: (r) => formatMoney(r.net, r.currency) },
    { header: t("common.margin"), align: "right", render: (r) => (r.margin_pct === null ? "—" : `${r.margin_pct} %`) },
    { header: t("common.rank"), align: "right", render: (r) => r.avg_rank ?? "—" },
    { header: t("common.avgPrice"), align: "right", render: (r) => formatMoney(r.avg_price, r.currency) },
  ];

  const nodes = tree.data?.nodes ?? [];

  return (
    <>
      <Card
        title={t("categories.title")}
        subtitle={t("categories.subtitle")}
        actions={<button className="btn-ghost" onClick={tree.reload}>{t("common.refresh")}</button>}
      >
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <label className="row">
            <span className="muted">{t("common.days")}</span>
            <select className="select" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {DAYS.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </label>
          <MarketFilter
            value={market}
            markets={(markets.data?.marketplaces ?? []).map((m) => m.code)}
            onChange={setMarket}
          />
        </div>
        <div className="muted" style={{ marginBottom: 10 }}>{t("categories.drillHint")}</div>

        {tree.error ? (
          <ErrorBox error={tree.error} onRetry={tree.reload} />
        ) : tree.loading && !tree.data ? (
          <Loading />
        ) : (
          <DataTable
            columns={columns}
            rows={nodes}
            rowKey={(r) => r.id}
            emptyMessage={t("categories.empty")}
          />
        )}
      </Card>

      {open !== null && nodes.some((n) => n.id === open && n.kind === "family") && (
        <Card title={t("categories.drill")}>
          <FamilyDrill family={nodes.find((n) => n.id === open)!} days={days} market={market} />
        </Card>
      )}
    </>
  );
}