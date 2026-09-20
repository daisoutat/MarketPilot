/* Bilingual i18n (EN/FR) — no external dependency.
   Every UI string lives in `en`; `fr` is typed against it so the two dictionaries
   can never drift (TypeScript fails the build if a key is missing). */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Lang = "en" | "fr";

const en = {
  "app.name": "MarketPilot",
  "app.tagline": "Amazon market intelligence · US & Canada",

  "nav.group.analytics": "Analytics",
  "nav.group.market": "Market research",
  "nav.group.system": "System",
  "nav.overview": "Overview",
  "nav.orders": "Orders",
  "nav.inventory": "Inventory",
  "nav.margin": "Margin",
  "nav.research": "Research",
  "nav.compare": "Cross-market",
  "nav.categories": "Categories",
  "nav.forecast": "Forecast",
  "nav.assistant": "Assistant",
  "nav.sync": "Pipeline health",
  "nav.alerts": "Alerts",

  "common.refresh": "Refresh",
  "common.loading": "Loading…",
  "common.retry": "Retry",
  "common.empty": "No data yet.",
  "common.noResults": "No results for these filters.",
  "common.market": "Market",
  "common.allMarkets": "All markets",
  "common.days": "Days",
  "common.search": "Search",
  "common.searchPlaceholder": "ASIN, title or buyer…",
  "common.apply": "Apply",
  "common.reset": "Reset",
  "common.total": "Total",
  "common.page": "Page",
  "common.of": "of",
  "common.prev": "Previous",
  "common.next": "Next",
  "common.asin": "ASIN",
  "common.title": "Title",
  "common.brand": "Brand",
  "common.price": "Price",
  "common.buybox": "Buy Box",
  "common.currency": "Currency",
  "common.quantity": "Qty",
  "common.units": "Units",
  "common.orders": "Orders",
  "common.gross": "Gross",
  "common.revenue": "Revenue",
  "common.fees": "Fees",
  "common.net": "Net",
  "common.margin": "Margin",
  "common.rank": "Rank",
  "common.captured": "Captured",
  "common.keywords": "Keywords",
  "common.status": "Status",
  "common.buyer": "Buyer",
  "common.location": "Location",
  "common.date": "Date",
  "common.save": "Save",
  "common.quick": "Quick",
  "common.volatility": "Volatility",
  "common.job": "Job",
  "common.rows": "Rows",
  "common.error": "Error",
  "common.started": "Started",
  "common.finished": "Finished",
  "common.priceUsd": "Price (USD)",
  "common.gapUsd": "Gap (USD)",
  "common.marketCount": "Markets",
  "common.lastSnap": "Last snapshot",
  "common.window": "Window",
  "common.avgPrice": "Avg. price",
  "common.minUnits": "Min. units",
  "common.health": "API health",
  "common.ok": "Operational",
  "common.degraded": "Degraded",
  "common.device": "Dashboard",
  "common.themeDark": "Dark",
  "common.themeLight": "Light",

  "overview.title": "Overview",
  "overview.subtitle": "Sales performance across marketplaces",
  "overview.kpi.orders": "Orders",
  "overview.kpi.units": "Units sold",
  "overview.kpi.gross": "Gross sales",
  "overview.kpi.markets": "Active markets",
  "overview.byMarket": "Performance by market",
  "overview.byStatus": "Orders by status",
  "overview.series": "Daily trend",
  "overview.series.caption": "Orders per day",
  "overview.units": "Units",
  "overview.gross": "Gross",

  "orders.title": "Orders",
  "orders.subtitle": "Order feed with per-order units and gross",
  "orders.amazonId": "Amazon order",
  "orders.showing": "Showing {from}–{to} of {total}",

  "inventory.title": "Inventory",
  "inventory.subtitle": "Latest listed price and stock per product",
  "inventory.listedPrice": "Listed price",

  "margin.title": "Margin analytics",
  "margin.subtitle": "Revenue, fees and net margin from settlement lines",
  "margin.byProduct": "Margin by product",
  "margin.marginPct": "Margin %",

  "research.title": "Market research",
  "research.subtitle": "Tracked ASINs (PA-API) with USD-normalized pricing",

  "compare.title": "Cross-market comparison",
  "compare.subtitle": "Latest US vs. CA prices, normalized to USD",
  "compare.market": "Market",
  "compare.gap": "US − CA",
  "compare.gapHint": "Positive gap = cheaper in Canada",

  "sync.title": "Pipeline health",
  "sync.subtitle": "Recent scheduled and webhook runs",
  "sync.job": "Job",
  "sync.market": "Market",
  "sync.status": "Status",
  "sync.rows": "Rows",
  "sync.started": "Started",
  "sync.finished": "Finished",
  "sync.error": "Error",
  "sync.status.done": "Done",
  "sync.status.error": "Error",
  "sync.status.running": "Running",
  "sync.status.skipped": "Skipped",

  "alerts.title": "Alerts & notifications",
  "alerts.subtitle": "Rule-based alerts across products and markets",
  "alerts.list": "Open alerts",
  "alerts.open": "Open",
  "alerts.critical": "Critical",
  "alerts.resolved": "Resolved",
  "alerts.all": "All",
  "alerts.resolve": "Resolve",
  "alerts.resolvedBy": "Resolved by {name}",
  "alerts.empty": "No alerts in this view.",
  "alerts.rules": "Detection rules",
  "alerts.rulesHint": "Rules run every few minutes; cooldown suppresses repeated alerts.",
  "alerts.kind": "Kind",
  "alerts.message": "Message",
  "alerts.inactive": "Off",
  "alerts.threshold": "Threshold",
  "alerts.cooldown": "Cooldown (h)",
  "alerts.enabled": "Active",
  "alerts.severity.info": "Info",
  "alerts.severity.warning": "Warning",
  "alerts.severity.critical": "Critical",
  "alerts.kind.price_drop": "Price drop",
  "alerts.kind.stock_out": "Stock-out",
  "alerts.kind.margin_erosion": "Margin erosion",
  "alerts.unit.price_drop": "% drop",
  "alerts.unit.stock_out": "units",
  "alerts.unit.margin_erosion": "margin %",
  "alerts.explain.price_drop": "Latest snapshot fell vs previous by the threshold.",
  "alerts.explain.stock_out": "Latest inventory quantity at or below the threshold.",
  "alerts.explain.margin_erosion": "90-day net margin below the threshold (negative).",

  "categories.title": "Multidimensional analysis",
  "categories.subtitle": "Category → niche → family rollups across products",
  "categories.tree": "Segment tree",
  "categories.level": "Level",
  "categories.products": "Products",
  "categories.family": "Family",
  "categories.drill": "Products in this family",
  "categories.drillHint": "Click a family to inspect its products; nested values roll up.",
  "categories.empty": "No taxonomy yet — the nightly categorize job builds the tree.",

  "forecast.title": "Demand forecast",
  "forecast.subtitle": "Explainable 30-day demand outlook per product",
  "forecast.list": "Latest forecasts",
  "forecast.detail": "Forecast detail",
  "forecast.mean": "Mean / day",
  "forecast.trend": "Trend / week",
  "forecast.volatility": "Volatility",
  "forecast.rmse": "Fit error (RMSE)",
  "forecast.window": "Window",
  "forecast.horizon": "Horizon",
  "forecast.nextTotal": "Next {n} days",
  "forecast.history": "Actuals",
  "forecast.outlook": "Forecast",
  "forecast.band": "80% confidence band",
  "forecast.explainTrend": "Demand {direction} {weekly} per week on average",
  "forecast.explainPeak": "Strongest day: {day}",
  "forecast.explainFlat": "Level demand, no significant weekly pattern yet.",
  "forecast.empty": "No forecasts yet — the nightly forecast job fits the models.",
  "forecast.days.weekday": "weekday demand",
  "forecast.seasonal.saturday": "Saturday",

  "assistant.title": "Intelligent assistant",
  "assistant.subtitle": "Ask in plain language — answers grounded in the live data",
  "assistant.placeholder": "Ask about sales, margins, forecasts…",
  "assistant.send": "Send",
  "assistant.clear": "New session",
  "assistant.loadingHistory": "Loading conversation…",
  "assistant.greeting": "Hi! I read the live database to answer sales, margins, inventory, alerts, cross-market prices and 30-day forecasts. Ask in one of the quick actions or your own words (EN/FR).",
  "assistant.you": "You",
  "assistant.intent": "Intent: {intent}",
  "assistant.emptyData": "No data for that.",
  "assistant.prefsTitle": "Customisation",
  "assistant.prefsHint": "These prefs shape every answer (market scope, reply language, digest).",
  "assistant.prefs.market": "Default market",
  "assistant.prefs.market.all": "All markets",
  "assistant.prefs.language": "Reply language",
  "assistant.prefs.currency": "Currency",
  "assistant.prefs.digest": "Digest",
  "assistant.prefs.digest.daily": "Daily",
  "assistant.prefs.digest.weekly": "Weekly",
  "assistant.prefs.digest.off": "Off",
  "assistant.prefs.saved": "Saved",
  "assistant.insightsTitle": "Predictive insights",
  "assistant.insightsHint": "Latest forecast rankings — gained & lost momentum, and next-30-day leaders.",
  "assistant.gainers": "Gaining momentum",
  "assistant.decliners": "Losing momentum",
  "assistant.trend": "Trend",
  "assistant.next30": "Next 30 days",
  "assistant.leaders": "Next-30-day leaders",
  "assistant.generated": "Generated {date}",
  "assistant.quick.summary": "Summary",
  "assistant.quick.orders": "Orders",
  "assistant.quick.margin": "Margin",
  "assistant.quick.forecast": "Forecast",
  "assistant.quick.alerts": "Alerts",
  "assistant.quick.inventory": "Inventory",
  "assistant.quick.compare": "Compare",
  "assistant.colOrders": "Order",
  "assistant.colMarket": "Mkt",
  "assistant.colStatus": "Status",
  "assistant.colUnits": "Units",
  "assistant.colGross": "Gross",
  "assistant.colAsin": "ASIN",
  "assistant.colTitle": "Title",
  "assistant.colRevenue": "Revenue",
  "assistant.colNet": "Net",
  "assistant.colMargin": "Margin",
  "assistant.colQty": "Qty",
  "assistant.colPrice": "Price",
  "assistant.colSeverity": "Severity",
  "assistant.colKind": "Kind",
  "assistant.colMessage": "Message",
  "assistant.colUs": "US",
  "assistant.colCa": "CA",
  "assistant.colGap": "Gap (USD)",
  "assistant.colProducts": "Products",
  "assistant.colSegment": "Segment",
  "assistant.colMean": "Mean/day",
  "assistant.rows": "rows",

  "status.Pending": "Pending",
  "status.Unshipped": "Unshipped",
  "status.PartiallyShipped": "Partially shipped",
  "status.Shipped": "Shipped",
  "status.Canceled": "Canceled",
  "status.Unfulfillable": "Unfulfillable",
  "status.InvoiceUnconfirmed": "Invoice unconfirmed",
  "status.active": "Active",
  "status.pending": "Pending",
  "status.error": "Error",

  "error.title": "Something went wrong",
  "error.network": "Cannot reach the API. Is the backend running?",
  "error.http": "Request failed ({status})",
} as const;

export type TranslationKey = keyof typeof en;

const fr: Record<TranslationKey, string> = {
  "app.name": "MarketPilot",
  "app.tagline": "Intelligence marché Amazon · É.-U. et Canada",

  "nav.group.analytics": "Analytique",
  "nav.group.market": "Étude de marché",
  "nav.group.system": "Système",
  "nav.overview": "Aperçu",
  "nav.orders": "Commandes",
  "nav.inventory": "Inventaire",
  "nav.margin": "Marge",
  "nav.research": "Recherche",
  "nav.compare": "Inter-marchés",
  "nav.categories": "Catégories",
  "nav.forecast": "Prévisions",
  "nav.assistant": "Assistante",
  "nav.sync": "Santé du pipeline",
  "nav.alerts": "Alertes",

  "common.refresh": "Actualiser",
  "common.loading": "Chargement…",
  "common.retry": "Réessayer",
  "common.empty": "Aucune donnée pour l'instant.",
  "common.noResults": "Aucun résultat pour ces filtres.",
  "common.market": "Marché",
  "common.allMarkets": "Tous les marchés",
  "common.days": "Jours",
  "common.search": "Rechercher",
  "common.searchPlaceholder": "ASIN, titre ou acheteur…",
  "common.apply": "Appliquer",
  "common.reset": "Réinitialiser",
  "common.total": "Total",
  "common.page": "Page",
  "common.of": "sur",
  "common.prev": "Précédent",
  "common.next": "Suivant",
  "common.asin": "ASIN",
  "common.title": "Titre",
  "common.brand": "Marque",
  "common.price": "Prix",
  "common.buybox": "Boîte d'achat",
  "common.currency": "Devise",
  "common.quantity": "Qté",
  "common.units": "Unités",
  "common.orders": "Commandes",
  "common.gross": "Brut",
  "common.revenue": "Revenus",
  "common.fees": "Frais",
  "common.net": "Net",
  "common.margin": "Marge",
  "common.rank": "Rang",
  "common.captured": "Capturé",
  "common.keywords": "Mots-clés",
  "common.status": "Statut",
  "common.buyer": "Acheteur",
  "common.location": "Lieu",
  "common.date": "Date",
  "common.save": "Enregistrer",
  "common.quick": "Rapides",
  "common.volatility": "Volatilité",
  "common.job": "Tâche",
  "common.rows": "Lignes",
  "common.error": "Erreur",
  "common.started": "Début",
  "common.finished": "Fin",
  "common.priceUsd": "Prix (USD)",
  "common.gapUsd": "Écart (USD)",
  "common.marketCount": "Marchés",
  "common.lastSnap": "Dernier instantané",
  "common.window": "Fenêtre",
  "common.avgPrice": "Prix moyen",
  "common.minUnits": "Unités min.",
  "common.health": "Santé de l'API",
  "common.ok": "Opérationnel",
  "common.degraded": "Dégradé",
  "common.device": "Tableau de bord",
  "common.themeDark": "Sombre",
  "common.themeLight": "Clair",

  "overview.title": "Aperçu",
  "overview.subtitle": "Performance des ventes par marché",
  "overview.kpi.orders": "Commandes",
  "overview.kpi.units": "Unités vendues",
  "overview.kpi.gross": "Ventes brutes",
  "overview.kpi.markets": "Marchés actifs",
  "overview.byMarket": "Performance par marché",
  "overview.byStatus": "Commandes par statut",
  "overview.series": "Tendance quotidienne",
  "overview.series.caption": "Commandes par jour",
  "overview.units": "Unités",
  "overview.gross": "Brut",

  "orders.title": "Commandes",
  "orders.subtitle": "Flux des commandes avec unités et brut par commande",
  "orders.amazonId": "Commande Amazon",
  "orders.showing": "Affichage {from}–{to} sur {total}",

  "inventory.title": "Inventaire",
  "inventory.subtitle": "Dernier prix affiché et stock par produit",
  "inventory.listedPrice": "Prix affiché",

  "margin.title": "Analyse des marges",
  "margin.subtitle": "Revenus, frais et marge nette des lignes de règlement",
  "margin.byProduct": "Marge par produit",
  "margin.marginPct": "Marge %",

  "research.title": "Étude de marché",
  "research.subtitle": "ASIN suivis (PA-API) avec prix normalisés en USD",

  "compare.title": "Comparaison inter-marchés",
  "compare.subtitle": "Derniers prix É.-U. c. Canada, normalisés en USD",
  "compare.market": "Marché",
  "compare.gap": "É.-U. − Canada",
  "compare.gapHint": "Écart positif = moins cher au Canada",

  "sync.title": "Santé du pipeline",
  "sync.subtitle": "Exécutions planifiées et webhooks récentes",
  "sync.job": "Tâche",
  "sync.market": "Marché",
  "sync.status": "Statut",
  "sync.rows": "Lignes",
  "sync.started": "Début",
  "sync.finished": "Fin",
  "sync.error": "Erreur",
  "sync.status.done": "Terminé",
  "sync.status.error": "Erreur",
  "sync.status.running": "En cours",
  "sync.status.skipped": "Ignoré",

  "alerts.title": "Alertes et notifications",
  "alerts.subtitle": "Alertes fondées sur des règles, par produit et marché",
  "alerts.list": "Alertes ouvertes",
  "alerts.open": "Ouvertes",
  "alerts.critical": "Critiques",
  "alerts.resolved": "Résolues",
  "alerts.all": "Toutes",
  "alerts.resolve": "Résoudre",
  "alerts.resolvedBy": "Résolue par {name}",
  "alerts.empty": "Aucune alerte dans cette vue.",
  "alerts.rules": "Règles de détection",
  "alerts.rulesHint": "Les règles s'exécutent toutes les quelques minutes ; le délai d'attente évite les alertes en double.",
  "alerts.kind": "Type",
  "alerts.message": "Message",
  "alerts.inactive": "Désactivée",
  "alerts.threshold": "Seuil",
  "alerts.cooldown": "Délai (h)",
  "alerts.enabled": "Active",
  "alerts.severity.info": "Info",
  "alerts.severity.warning": "Avertissement",
  "alerts.severity.critical": "Critique",
  "alerts.kind.price_drop": "Baisse de prix",
  "alerts.kind.stock_out": "Rupture de stock",
  "alerts.kind.margin_erosion": "Érosion de marge",
  "alerts.unit.price_drop": "baisse %",
  "alerts.unit.stock_out": "unités",
  "alerts.unit.margin_erosion": "marge %",
  "alerts.explain.price_drop": "Le dernier instantané a baissé par rapport au précédent selon le seuil.",
  "alerts.explain.stock_out": "Quantité d'inventaire la plus récente égale ou inférieure au seuil.",
  "alerts.explain.margin_erosion": "Marge nette sur 90 jours sous le seuil (négatif).",

  "categories.title": "Analyse multidimensionnelle",
  "categories.subtitle": "Catégorie → niche → famille, agrégations par produit",
  "categories.tree": "Arbre des segments",
  "categories.level": "Niveau",
  "categories.products": "Produits",
  "categories.family": "Famille",
  "categories.drill": "Produits de cette famille",
  "categories.drillHint": "Cliquez une famille pour voir ses produits ; les valeurs remontent d'un niveau.",
  "categories.empty": "Aucune taxonomie — le lot nocturne construit l'arbre.",

  "forecast.title": "Prévision de la demande",
  "forecast.subtitle": "Perspective de demande à 30 jours, expliquée par produit",
  "forecast.list": "Dernières prévisions",
  "forecast.detail": "Détail de la prévision",
  "forecast.mean": "Moyenne / jour",
  "forecast.trend": "Tendance / semaine",
  "forecast.volatility": "Volatilité",
  "forecast.rmse": "Erreur d'ajustement (RMSE)",
  "forecast.window": "Fenêtre",
  "forecast.horizon": "Horizon",
  "forecast.nextTotal": "Prochains {n} jours",
  "forecast.history": "Réels",
  "forecast.outlook": "Prévision",
  "forecast.band": "Intervalle de confiance 80 %",
  "forecast.explainTrend": "Demande {direction} de {weekly} par semaine en moyenne",
  "forecast.explainPeak": "Journée forte : {day}",
  "forecast.explainFlat": "Demande stable, pas de motif hebdomadaire marqué.",
  "forecast.empty": "Aucune prévision — le lot nocturne ajuste les modèles.",
  "forecast.days.weekday": "demande en semaine",
  "forecast.seasonal.saturday": "Samedi",

  "assistant.title": "Assistante intelligente",
  "assistant.subtitle": "Posez une question en langage naturel — réponses fondées sur les données en direct",
  "assistant.placeholder": "Parlez des ventes, marges, prévisions…",
  "assistant.send": "Envoyer",
  "assistant.clear": "Nouvelle session",
  "assistant.loadingHistory": "Chargement de la conversation…",
  "assistant.greeting": "Bonjour ! Je lis la base en direct pour répondre sur les ventes, marges, stocks, alertes, prix inter-marchés et prévisions à 30 jours. Tenez une action rapide ou posez votre question (FR/EN).",
  "assistant.you": "Vous",
  "assistant.intent": "Intention : {intent}",
  "assistant.emptyData": "Aucune donnée pour cela.",
  "assistant.prefsTitle": "Personnalisation",
  "assistant.prefsHint": "Ces préférences façonnent chaque réponse (marché par défaut, langue de réponse, digest).",
  "assistant.prefs.market": "Marché par défaut",
  "assistant.prefs.market.all": "Tous les marchés",
  "assistant.prefs.language": "Langue de réponse",
  "assistant.prefs.currency": "Devise",
  "assistant.prefs.digest": "Digest",
  "assistant.prefs.digest.daily": "Quotidien",
  "assistant.prefs.digest.weekly": "Hebdomadaire",
  "assistant.prefs.digest.off": "Désactivé",
  "assistant.prefs.saved": "Enregistré",
  "assistant.insightsTitle": "Informations prédictives",
  "assistant.insightsHint": "Derniers classements des prévisions — élan gagné et perdu, chefs de file des 30 prochains jours.",
  "assistant.gainers": "Élan à la hausse",
  "assistant.decliners": "Élan à la baisse",
  "assistant.trend": "Tendance",
  "assistant.next30": "Prochains 30 jours",
  "assistant.leaders": "Chefs de file (30 jours)",
  "assistant.generated": "Généré {date}",
  "assistant.quick.summary": "Synthèse",
  "assistant.quick.orders": "Commandes",
  "assistant.quick.margin": "Marge",
  "assistant.quick.forecast": "Prévisions",
  "assistant.quick.alerts": "Alertes",
  "assistant.quick.inventory": "Inventaire",
  "assistant.quick.compare": "Comparer",
  "assistant.colOrders": "Commande",
  "assistant.colMarket": "Marché",
  "assistant.colStatus": "Statut",
  "assistant.colUnits": "Unités",
  "assistant.colGross": "Brut",
  "assistant.colAsin": "ASIN",
  "assistant.colTitle": "Titre",
  "assistant.colRevenue": "Revenus",
  "assistant.colNet": "Net",
  "assistant.colMargin": "Marge",
  "assistant.colQty": "Qté",
  "assistant.colPrice": "Prix",
  "assistant.colSeverity": "Sévérité",
  "assistant.colKind": "Type",
  "assistant.colMessage": "Message",
  "assistant.colUs": "É.-U.",
  "assistant.colCa": "CA",
  "assistant.colGap": "Écart (USD)",
  "assistant.colProducts": "Produits",
  "assistant.colSegment": "Segment",
  "assistant.colMean": "Moyenne/jour",
  "assistant.rows": "lignes",

  "status.Pending": "En attente",
  "status.Unshipped": "Non expédiée",
  "status.PartiallyShipped": "Partiellement expédiée",
  "status.Shipped": "Expédiée",
  "status.Canceled": "Annulée",
  "status.Unfulfillable": "Non exécutable",
  "status.InvoiceUnconfirmed": "Facture non confirmée",
  "status.active": "Actif",
  "status.pending": "En attente",
  "status.error": "Erreur",

  "error.title": "Une erreur est survenue",
  "error.network": "Impossible de joindre l'API. Le serveur est-il démarré ?",
  "error.http": "Échec de la requête ({status})",
};

const DICTS: Record<Lang, Record<TranslationKey, string>> = { en, fr };

export function detectLang(): Lang {
  try {
    const stored = localStorage.getItem("mp.lang");
    if (stored === "fr" || stored === "en") return stored;
  } catch {
    /* localStorage unavailable (SSR/tests) */
  }
  const nav = typeof navigator !== "undefined" ? navigator.language : "en";
  return nav.toLowerCase().startsWith("fr") ? "fr" : "en";
}

type I18nValue = {
  lang: Lang;
  locale: string;
  setLang: (lang: Lang) => void;
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string;
  /** Translate a dynamic value (e.g. order status), falling back to the raw value. */
  tf: (prefix: string, value: string) => string;
  formatMoney: (amount: number | null | undefined, currency?: string) => string;
  formatInt: (value: number | null | undefined) => string;
  formatNumber: (value: number | null | undefined, digits?: number) => string;
  formatPercent: (value: number | null | undefined, digits?: number) => string;
  formatDate: (iso: string | null | undefined, withTime?: boolean) => string;
};

const Ctx = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectLang);

  useEffect(() => {
    try {
      localStorage.setItem("mp.lang", lang);
    } catch {
      /* ignore */
    }
    if (typeof document !== "undefined") document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => setLangState(next), []);
  const locale = lang === "fr" ? "fr-CA" : "en-CA";

  const t = useCallback(
    (key: TranslationKey, vars?: Record<string, string | number>) => {
      const raw = DICTS[lang][key] ?? key;
      if (!vars) return raw;
      return raw.replace(/\{(\w+)\}/g, (_m, name: string) =>
        name in vars ? String(vars[name]) : `{${name}}`
      );
    },
    [lang]
  );

  const tf = useCallback(
    (prefix: string, value: string) => {
      const key = `${prefix}.${value}` as TranslationKey;
      return DICTS[lang][key] ?? value;
    },
    [lang]
  );

  const formatMoney = useCallback(
    (amount: number | null | undefined, currency = "USD") => {
      if (amount === null || amount === undefined || Number.isNaN(amount)) return "—";
      try {
        return new Intl.NumberFormat(locale, {
          style: "currency",
          currency: currency || "USD",
          maximumFractionDigits: 2,
        }).format(amount);
      } catch {
        return `${amount.toFixed(2)} ${currency}`;
      }
    },
    [locale]
  );

  const formatInt = useCallback(
    (value: number | null | undefined) =>
      value === null || value === undefined || Number.isNaN(value)
        ? "—"
        : new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value),
    [locale]
  );

  const formatNumber = useCallback(
    (value: number | null | undefined, digits = 1) =>
      value === null || value === undefined || Number.isNaN(value)
        ? "—"
        : new Intl.NumberFormat(locale, {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
          }).format(value),
    [locale]
  );

  const formatPercent = useCallback(
    (value: number | null | undefined, digits = 1) =>
      value === null || value === undefined || Number.isNaN(value)
        ? "—"
        : `${new Intl.NumberFormat(locale, {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
          }).format(value)} %`,
    [locale]
  );

  const formatDate = useCallback(
    (iso: string | null | undefined, withTime = false) => {
      if (!iso) return "—";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      return d.toLocaleString(locale, {
        dateStyle: "medium",
        ...(withTime ? { timeStyle: "short" as const } : {}),
      });
    },
    [locale]
  );

  const value = useMemo<I18nValue>(
    () => ({
      lang, locale, setLang, t, tf,
      formatMoney, formatInt, formatNumber, formatPercent, formatDate,
    }),
    [lang, locale, setLang, t, tf, formatMoney, formatInt, formatNumber, formatPercent, formatDate]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n must be used within <I18nProvider>");
  return ctx;
}
