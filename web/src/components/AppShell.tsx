import type { ReactNode } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks";
import { useI18n, type TranslationKey } from "../i18n";
import { navigate, type Route } from "../router";
import { Icon, LanguageToggle, Spinner, ThemeToggle, type IconName } from "./ui";
import { useTheme } from "../hooks";

const NAV: { group: TranslationKey; items: { route: Route; icon: IconName }[] }[] = [
  {
    group: "nav.group.analytics",
    items: [
      { route: "overview", icon: "overview" },
      { route: "orders", icon: "orders" },
      { route: "inventory", icon: "inventory" },
      { route: "margin", icon: "margin" },
      { route: "categories", icon: "categories" },
      { route: "forecast", icon: "forecast" },
      { route: "assistant", icon: "assistant" },
    ],
  },
  {
    group: "nav.group.market",
    items: [
      { route: "research", icon: "research" },
      { route: "compare", icon: "compare" },
    ],
  },
  { group: "nav.group.system", items: [
    { route: "sync", icon: "sync" },
    { route: "alerts", icon: "alerts" },
  ] },
];

function HealthChip() {
  const { t } = useI18n();
  const { data, loading, error } = useApi(api.health, []);
  if (loading) return <span className="chip"><Spinner /> {t("common.health")}</span>;
  const ok = !!data && !error && data.status === "ok";
  return (
    <span className={`chip ${ok ? "good" : "bad"}`} title={t("common.health")}>
      {ok ? t("common.ok") : t("common.degraded")}
    </span>
  );
}

export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  const { t, lang, setLang } = useI18n();
  const { theme, toggle } = useTheme();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">M</div>
          <div>
            <div className="brand-name">{t("app.name")}</div>
            <div className="brand-sub">{t("app.tagline")}</div>
          </div>
        </div>

        <nav className="nav">
          {NAV.map((section) => (
            <div key={section.group}>
              <div className="nav-group">{t(section.group)}</div>
              {section.items.map((item) => (
                <a
                  key={item.route}
                  href={`#/${item.route}`}
                  className={`nav-item ${route === item.route ? "active" : ""}`}
                  onClick={(e) => {
                    e.preventDefault();
                    navigate(item.route);
                  }}
                >
                  <span className="nav-icon"><Icon name={item.icon} size={17} /></span>
                  {t(`nav.${item.route}` as TranslationKey)}
                </a>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <LanguageToggle lang={lang} onChange={setLang} />
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>{t(`nav.${route}` as TranslationKey)}</h1>
          <span className="spacer" />
          <HealthChip />
          <ThemeToggle theme={theme} onToggle={toggle} />
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
