/* Dependency-free SVG charts. Values must be pre-aggregated by the caller. */
import { useI18n } from "../i18n";
import { Empty } from "./ui";

export interface ChartPoint {
  label: string;
  value: number;
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(value)));
  const n = value / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

export function LineChart({
  points, height = 220, formatValue,
}: {
  points: ChartPoint[];
  height?: number;
  formatValue?: (value: number) => string;
}) {
  const { formatInt } = useI18n();
  if (!points.length) return <Empty />;

  const width = 720;
  const pad = { l: 48, r: 16, t: 16, b: 30 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const max = niceMax(Math.max(...points.map((p) => p.value), 0));
  const x = (i: number) => pad.l + (points.length === 1 ? innerW / 2 : (i * innerW) / (points.length - 1));
  const y = (v: number) => pad.t + (1 - v / max) * innerH;

  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(points.length - 1).toFixed(1)},${(pad.t + innerH).toFixed(1)} L${x(0).toFixed(1)},${(pad.t + innerH).toFixed(1)} Z`;
  const gridCount = 4;
  const labelEvery = Math.ceil(points.length / 7);

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" preserveAspectRatio="none">
      {Array.from({ length: gridCount + 1 }, (_, i) => {
        const value = (max / gridCount) * i;
        return (
          <g key={i}>
            <line className="grid-line" x1={pad.l} x2={width - pad.r} y1={y(value)} y2={y(value)} />
            <text className="axis-text" x={pad.l - 6} y={y(value) + 3} textAnchor="end">
              {formatValue ? formatValue(value) : formatInt(value)}
            </text>
          </g>
        );
      })}
      <path className="area" d={area} />
      <path className="line" d={line} />
      {points.map((p, i) =>
        i % labelEvery === 0 ? (
          <text key={`x${i}`} className="axis-text" x={x(i)} y={height - 10} textAnchor="middle">
            {p.label}
          </text>
        ) : null
      )}
    </svg>
  );
}

export function BarChart({
  items, height = 220, formatValue,
}: {
  items: ChartPoint[];
  height?: number;
  formatValue?: (value: number) => string;
}) {
  const { formatInt } = useI18n();
  if (!items.length) return <Empty />;

  const width = 720;
  const pad = { l: 48, r: 16, t: 16, b: 30 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const max = niceMax(Math.max(...items.map((i) => i.value), 0));
  const slot = innerW / items.length;
  const barW = Math.min(54, slot * 0.6);
  const labelEvery = Math.ceil(items.length / 12);

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" preserveAspectRatio="none">
      {Array.from({ length: 5 }, (_, i) => {
        const value = (max / 4) * i;
        const yy = pad.t + (1 - value / max) * innerH;
        return (
          <g key={i}>
            <line className="grid-line" x1={pad.l} x2={width - pad.r} y1={yy} y2={yy} />
            <text className="axis-text" x={pad.l - 6} y={yy + 3} textAnchor="end">
              {formatValue ? formatValue(value) : formatInt(value)}
            </text>
          </g>
        );
      })}
      {items.map((item, i) => {
        const h = max > 0 ? (item.value / max) * innerH : 0;
        const xx = pad.l + slot * i + (slot - barW) / 2;
        const yy = pad.t + innerH - h;
        return (
          <g key={item.label + i}>
            <rect className="bar" x={xx} y={yy} width={barW} height={Math.max(h, 0)} rx={4}>
              <title>{`${item.label}: ${formatValue ? formatValue(item.value) : formatInt(item.value)}`}</title>
            </rect>
            {i % labelEvery === 0 && (
              <text className="axis-text" x={pad.l + slot * i + slot / 2} y={height - 10} textAnchor="middle">
                {item.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function ForecastChart({
  series, height = 230, formatValue,
}: {
  /**
   * Combined actual + forecast series. Values carry `actual` (history) and
   * `forecast` (outlook); hi/lo draw the 80% confidence band on forecast points.
   */
  series: {
    label: string;
    actual?: number | null;
    forecast?: number | null;
    lo?: number | null;
    hi?: number | null;
  }[];
  height?: number;
  formatValue?: (value: number) => string;
}) {
  const { formatInt } = useI18n();
  if (!series.length) return <Empty />;

  const width = 720;
  const pad = { l: 48, r: 16, t: 16, b: 30 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const allValues = series.flatMap((p) => [p.actual, p.forecast, p.lo, p.hi].filter((v) => v !== null && v !== undefined));
  const max = niceMax(Math.max(0, ...(allValues as number[])));
  const x = (i: number) => pad.l + (series.length === 1 ? innerW / 2 : (i * innerW) / (series.length - 1));
  const y = (v: number) => pad.t + (1 - v / max) * innerH;

  const actualPts = series
    .map((p, i) => (p.actual !== null && p.actual !== undefined ? { i, v: p.actual } : null))
    .filter(Boolean) as { i: number; v: number }[];
  const fcPts = series
    .map((p, i) => (p.forecast !== null && p.forecast !== undefined ? { i, v: p.forecast } : null))
    .filter(Boolean) as { i: number; v: number }[];
  const path = (pts: { i: number; v: number }[]) =>
    pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");

  const topPts = series
    .map((p, i) => (p.hi !== null && p.hi !== undefined ? { i, v: p.hi } : null))
    .filter(Boolean) as { i: number; v: number }[];
  const botPts = series
    .map((p, i) => (p.lo !== null && p.lo !== undefined ? { i, v: p.lo } : null))
    .filter(Boolean) as { i: number; v: number }[];
  const bandD =
    topPts.length && botPts.length
      ? `${path(topPts)} ${[...botPts]
          .reverse()
          .map((p) => `L${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`)
          .join(" ")} Z`
      : "";

  const labelEvery = Math.ceil(series.length / 8);

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" preserveAspectRatio="none">
      {Array.from({ length: 5 }, (_, i) => {
        const value = (max / 4) * i;
        return (
          <g key={i}>
            <line className="grid-line" x1={pad.l} x2={width - pad.r} y1={y(value)} y2={y(value)} />
            <text className="axis-text" x={pad.l - 6} y={y(value) + 3} textAnchor="end">
              {formatValue ? formatValue(value) : formatInt(value)}
            </text>
          </g>
        );
      })}
      {bandD && <path className="band" d={bandD} />}
      {actualPts.length > 1 && <path className="actual-line" d={path(actualPts)} />}
      {fcPts.length > 1 && <path className="forecast-line" d={path(fcPts)} />}
      {series.map((p, i) =>
        i % labelEvery === 0 || i === series.length - 1 ? (
          <text key={`x${i}`} className="axis-text" x={x(i)} y={height - 10} textAnchor="middle">
            {p.label}
          </text>
        ) : null
      )}
    </svg>
  );
}
