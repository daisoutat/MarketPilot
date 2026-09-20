import { AppShell } from "./components/AppShell";
import { I18nProvider } from "./i18n";
import { useHashRoute } from "./router";
import AlertsPage from "./pages/Alerts";
import AssistantPage from "./pages/Assistant";
import CategoriesPage from "./pages/Categories";
import ComparePage from "./pages/Compare";
import ForecastPage from "./pages/Forecast";
import InventoryPage from "./pages/Inventory";
import MarginPage from "./pages/Margin";
import OrdersPage from "./pages/Orders";
import OverviewPage from "./pages/Overview";
import ResearchPage from "./pages/Research";
import SyncPage from "./pages/Sync";

function Router() {
  const route = useHashRoute();
  return (
    <AppShell route={route}>
      {route === "overview" && <OverviewPage />}
      {route === "orders" && <OrdersPage />}
      {route === "inventory" && <InventoryPage />}
      {route === "margin" && <MarginPage />}
      {route === "research" && <ResearchPage />}
      {route === "compare" && <ComparePage />}
      {route === "categories" && <CategoriesPage />}
      {route === "forecast" && <ForecastPage />}
      {route === "assistant" && <AssistantPage />}
      {route === "sync" && <SyncPage />}
      {route === "alerts" && <AlertsPage />}
    </AppShell>
  );
}

export default function App() {
  return (
    <I18nProvider>
      <Router />
    </I18nProvider>
  );
}
