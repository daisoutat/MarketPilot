/* Minimal dependency-free hash router. Routes look like `#/orders`. */
import { useCallback, useEffect, useState, type AnchorHTMLAttributes } from "react";

export type Route =
  | "overview"
  | "orders"
  | "inventory"
  | "margin"
  | "research"
  | "compare"
  | "categories"
  | "forecast"
  | "assistant"
  | "sync"
  | "alerts";

const ROUTES: Route[] = ["overview", "orders", "inventory", "margin", "research", "compare", "categories", "forecast", "assistant", "sync", "alerts"];
export const DEFAULT_ROUTE: Route = "overview";

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return (ROUTES as string[]).includes(raw) ? (raw as Route) : DEFAULT_ROUTE;
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() =>
    typeof window === "undefined" ? DEFAULT_ROUTE : parseHash()
  );

  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onChange);
    if (!window.location.hash) window.location.hash = `#/${DEFAULT_ROUTE}`;
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return route;
}

export function navigate(route: Route): void {
  if (window.location.hash !== `#/${route}`) window.location.hash = `#/${route}`;
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: Route };

export function Link({ to, children, ...rest }: LinkProps) {
  const onClick = useCallback(
    (e: React.MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      navigate(to);
      rest.onClick?.(e);
    },
    [to, rest]
  );
  return (
    <a href={`#/${to}`} {...rest} onClick={onClick}>
      {children}
    </a>
  );
}
