import { useState } from "react";
import { api } from "../api/client";
import { Card, DataTable, ErrorBox, Loading, MarketFilter, type Column } from "../components/ui";
import { useApi } from "../hooks";
import { useI18n, type TranslationKey } from "../i18n";
import type { AlertItem, AlertRuleItem, AlertSeverity } from "../api/types";

const KINDS: ("price_drop" | "stock_out" | "margin_erosion")[] = ["price_drop", "stock_out", "margin_erosion"];

const SEV_CLS: Record<AlertSeverity, string> = {
  info: "good",
  warning: "warn",
  critical: "bad",
};

function KindChip({ kind }: { kind: string }) {
  const { t } = useI18n();
  return <span className="chip">{t(`alerts.kind.${kind}` as TranslationKey)}</span>;
}

function SeverityChip({ severity }: { severity: AlertSeverity }) {
  const { t } = useI18n();
  return <span className={`chip ${SEV_CLS[severity] ?? ""}`}>{t(`alerts.severity.${severity}` as TranslationKey)}</span>;
}

export default function AlertsPage() {
  const { t, formatDate } = useI18n();
  const [status, setStatus] = useState<"open" | "resolved" | "all">("open");
  const [kind, setKind] = useState("");
  const [market, setMarket] = useState("");

  const markets = useApi(api.marketplaces, []);
  const alerts = useApi(
    () => api.alerts({ kind: kind || undefined, market: market || undefined, status }),
    [kind, market, status]
  );
  const rules = useApi(api.alertRules, []);

  async function resolve(id: number) {
    try {
      await api.resolveAlert(id);
      alerts.reload();
    } catch {
      /* surfaced by the table not being updated; reload keeps state truthful */
    }
  }

  async function patchRule(rule: AlertRuleItem, patch: Partial<{ threshold: number; cooldown_hours: number; enabled: boolean }>) {
    try {
      await api.updateAlertRule(rule.id, patch);
    } finally {
      rules.reload();
    }
  }

  const columns: Column<AlertItem>[] = [
    { header: t("alerts.kind"), render: (r) => <KindChip kind={r.kind} /> },
    { header: t("common.status"), render: (r) => <SeverityChip severity={r.severity} /> },
    { header: t("alerts.message"), render: (r) => <span className="muted">{r.message}</span> },
    { header: t("common.asin"), render: (r) => (r.asin ? <span className="mono">{r.asin}</span> : "—") },
    { header: t("common.market"), render: (r) => (r.market ? <strong>{r.market}</strong> : "—") },
    { header: t("common.date"), render: (r) => formatDate(r.created_at, true) },
    {
      header: "",
      align: "right",
      render: (r) =>
        r.resolved_at ? (
          <span className="muted" title={t("alerts.resolvedBy", { name: r.resolved_by || "—" })}>
            ✓
          </span>
        ) : (
          <button className="btn-ghost" onClick={() => resolve(r.id)}>{t("alerts.resolve")}</button>
        ),
    },
  ];

  return (
    <>
      <Card
        title={t("alerts.title")}
        subtitle={t("alerts.subtitle")}
        actions={<button className="btn-ghost" onClick={alerts.reload}>{t("common.refresh")}</button>}
      >
        <div className="toolbar" style={{ marginBottom: 12 }}>
          {(["open", "resolved", "all"] as const).map((s) => (
            <button
              key={s}
              className={`btn-ghost ${status === s ? "active" : ""}`}
              onClick={() => setStatus(s)}
            >
              {t(`alerts.${s}`)}
            </button>
          ))}
          <span className="spacer" />
          <label className="row">
            <span className="muted">{t("alerts.kind")}</span>
            <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">{t("common.allMarkets")}</option>
              {KINDS.map((k) => (
                <option key={k} value={k}>{t(`alerts.kind.${k}` as TranslationKey)}</option>
              ))}
            </select>
          </label>
          <MarketFilter
            value={market}
            markets={(markets.data?.marketplaces ?? []).map((m) => m.code)}
            onChange={setMarket}
          />
        </div>

        {alerts.error ? (
          <ErrorBox error={alerts.error} onRetry={alerts.reload} />
        ) : alerts.loading && !alerts.data ? (
          <Loading />
        ) : (
          <DataTable columns={columns} rows={alerts.data?.rows ?? []} rowKey={(r) => r.id} emptyMessage={t("alerts.empty")} />
        )}
      </Card>

      <Card title={t("alerts.rules")} subtitle={t("alerts.rulesHint")}>
        {rules.error ? (
          <ErrorBox error={rules.error} onRetry={rules.reload} />
        ) : rules.loading && !rules.data ? (
          <Loading />
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
            {(rules.data?.rules ?? []).map((rule) => (
              <div key={rule.id} className="card-inner">
                <div className="row wrap" style={{ marginBottom: 8 }}>
                  <strong>{t(`alerts.kind.${rule.kind}` as TranslationKey)}</strong>
                  <span className="spacer" />
                  <button
                    className={`btn-ghost ${rule.enabled ? "good" : ""}`}
                    onClick={() => patchRule(rule, { enabled: !rule.enabled })}
                  >
                    {rule.enabled ? t("alerts.enabled") : t("alerts.inactive")}
                  </button>
                </div>
                <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
                  {t(`alerts.explain.${rule.kind}` as TranslationKey)}
                </div>
                <div className="row wrap">
                  <label className="row">
                    <span className="muted">{t("alerts.threshold")} ({t(`alerts.unit.${rule.kind}` as TranslationKey)})</span>
                    <input
                      className="input"
                      type="number"
                      step="0.5"
                      defaultValue={rule.threshold}
                      onBlur={(e) => {
                        const v = Number(e.target.value);
                        if (v !== rule.threshold && !Number.isNaN(v)) patchRule(rule, { threshold: v });
                      }}
                      style={{ width: 90 }}
                    />
                  </label>
                  <label className="row">
                    <span className="muted">{t("alerts.cooldown")}</span>
                    <input
                      className="input"
                      type="number"
                      step="1"
                      min={1}
                      defaultValue={rule.cooldown_hours}
                      onBlur={(e) => {
                        const v = Number(e.target.value);
                        if (v !== rule.cooldown_hours && !Number.isNaN(v)) patchRule(rule, { cooldown_hours: v });
                      }}
                      style={{ width: 70 }}
                    />
                  </label>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}