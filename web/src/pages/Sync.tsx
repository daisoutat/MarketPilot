import { useEffect } from "react";
import { api } from "../api/client";
import { Card, DataTable, ErrorBox, Loading, StatusChip, type Column } from "../components/ui";
import { useApi } from "../hooks";
import { useI18n } from "../i18n";
import type { SyncRun } from "../api/types";

const REFRESH_MS = 30_000;

export default function SyncPage() {
  const { t, formatDate, formatInt } = useI18n();
  const sync = useApi(() => api.sync(30), []);

  // Keep the health view live without a manual refresh.
  useEffect(() => {
    const id = window.setInterval(sync.reload, REFRESH_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const columns: Column<SyncRun>[] = [
    { header: t("sync.job"), render: (r) => <span className="mono">{r.job}</span> },
    { header: t("sync.market"), render: (r) => r.market || "—" },
    { header: t("sync.status"), render: (r) => <StatusChip status={r.status} /> },
    { header: t("sync.rows"), align: "right", render: (r) => formatInt(r.rows) },
    { header: t("sync.started"), render: (r) => formatDate(r.started, true) },
    { header: t("sync.finished"), render: (r) => formatDate(r.finished, true) },
    {
      header: t("sync.error"),
      render: (r) => (r.error ? <span className="neg" title={r.error}>{r.error.slice(0, 60)}</span> : "—"),
    },
  ];

  return (
    <Card
      title={t("sync.title")}
      subtitle={t("sync.subtitle")}
      actions={<button className="btn-ghost" onClick={sync.reload}>{t("common.refresh")}</button>}
    >
      {sync.error ? (
        <ErrorBox error={sync.error} onRetry={sync.reload} />
      ) : sync.loading && !sync.data ? (
        <Loading />
      ) : (
        <DataTable columns={columns} rows={sync.data?.runs ?? []} rowKey={(r) => `${r.job}-${r.market}-${r.started}`} />
      )}
    </Card>
  );
}
