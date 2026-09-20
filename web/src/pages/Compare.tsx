import { useState } from "react";
import { api } from "../api/client";
import { Card, Empty, ErrorBox, Loading } from "../components/ui";
import { useApi, useDebounced } from "../hooks";
import { useI18n } from "../i18n";

export default function ComparePage() {
  const { t, formatDate, formatInt, formatMoney } = useI18n();
  const [asin, setAsin] = useState("");
  const [q, setQ] = useState("");
  const debouncedQ = useDebounced(q);

  const compare = useApi(
    () => api.compare({ asin: asin.trim() || undefined, q: debouncedQ || undefined, limit: 50 }),
    [asin, debouncedQ]
  );

  const rows = compare.data?.rows ?? [];

  return (
    <Card
      title={t("compare.title")}
      subtitle={t("compare.subtitle")}
      actions={<button className="btn-ghost" onClick={compare.reload}>{t("common.refresh")}</button>}
    >
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <input
          className="input mono"
          placeholder={t("common.asin")}
          value={asin}
          onChange={(e) => setAsin(e.target.value.toUpperCase())}
          style={{ width: 150 }}
        />
        <input
          className="input"
          placeholder={t("common.searchPlaceholder")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <span className="spacer" />
        <span className="muted">{t("compare.gapHint")}</span>
      </div>

      {compare.error ? (
        <ErrorBox error={compare.error} onRetry={compare.reload} />
      ) : compare.loading && !compare.data ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty message={t("common.noResults")} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {rows.map((row) => (
            <div key={row.asin}>
              <div className="row wrap" style={{ marginBottom: 8 }}>
                <span className="mono"><strong>{row.asin}</strong></span>
                <span>{row.title || "—"}</span>
                {row.brand && <span className="chip">{row.brand}</span>}
                <span className="spacer" />
                <span
                  className={`chip ${row.gap_usd === null ? "" : row.gap_usd > 0 ? "good" : "bad"}`}
                  title={t("compare.gapHint")}
                >
                  {t("compare.gap")}: {row.gap_usd === null ? "—" : formatMoney(row.gap_usd, "USD")}
                </span>
              </div>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>{t("compare.market")}</th>
                      <th className="num">{t("common.price")}</th>
                      <th className="num">{t("common.priceUsd")}</th>
                      <th className="num">{t("common.buybox")}</th>
                      <th className="num">{t("common.rank")}</th>
                      <th>{t("common.captured")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row.markets.map((m) => (
                      <tr key={m.market}>
                        <td><strong>{m.market}</strong></td>
                        <td className="num">{formatMoney(m.price, m.currency)}</td>
                        <td className="num"><strong>{formatMoney(m.price_usd, "USD")}</strong></td>
                        <td className="num">{m.buybox === null ? "—" : formatMoney(m.buybox, m.currency)}</td>
                        <td className="num">{m.sales_rank === null ? "—" : formatInt(m.sales_rank)}</td>
                        <td>{formatDate(m.captured_at, true)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
