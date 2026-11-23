import { Activity, Timer, Zap } from "lucide-react";
import { useId, useState } from "react";
import type { ModelPerformance, TokenBucket } from "../api/types";
import { smoothLine, type ChartModel } from "../lib/charts";
import { dateFormat, formatBucketTime, formatNumber } from "../lib/utils";

const metrics = [
  {
    key: "error_rate",
    title: "调用错误率",
    unit: "%",
    icon: Activity,
    hint: "失败调用数占该模型调用数的比例，同一候选模型的内部重试合并为一次。",
  },
  {
    key: "tps",
    title: "生成速度",
    unit: "tokens/s",
    icon: Zap,
    hint: "按流式生成时长加权的输出速度，扣除首个 token。需要提供商返回输出 token 数。",
  },
  {
    key: "ttft_ms",
    title: "首字延迟",
    unit: "ms",
    icon: Timer,
    hint: "从发出请求到收到首段文本、推理或工具输出的平均时间。",
  },
] as const;

const left = 34,
  right = 306,
  top = 10,
  bottom = 130;
type Point = { x: number; y: number };
const hourLabel = () =>
  dateFormat({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const dayLabel = () => dateFormat({ month: "2-digit", day: "2-digit" });
function segments(
  values: (number | null)[],
  x: (i: number) => number,
  y: (v: number) => number,
) {
  const runs: Point[][] = [];
  values.forEach((v, i) => {
    if (v === null) return;
    if (i === 0 || values[i - 1] === null) runs.push([]);
    runs[runs.length - 1].push({ x: x(i), y: y(v) });
  });
  return runs;
}

function axisMax(value: number) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  return (
    ([1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((n) => n * power >= value) ??
      10) * power
  );
}

function display(value: number, key: keyof ModelPerformance) {
  return formatNumber(value, {
    maximumFractionDigits: key === "ttft_ms" ? 0 : key === "error_rate" ? 2 : 1,
  });
}

function PerformanceCard({
  metric,
  rows,
  models,
  daily,
}: {
  metric: (typeof metrics)[number];
  rows: TokenBucket[];
  models: ChartModel[];
  daily: boolean;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const series = models.map((model) => ({
    ...model,
    values: rows.map((row) => {
      const v = row.model_performance?.[model.id]?.[metric.key];
      return v == null ? null : metric.key === "error_rate" ? v * 100 : v;
    }),
  }));
  const samples = series.flatMap((s) =>
    s.values.filter((v): v is number => v !== null),
  );
  const max = axisMax(samples.reduce((peak, v) => Math.max(peak, v), 0));
  const x = (i: number) =>
    left + ((right - left) * i) / Math.max(1, rows.length - 1);
  const y = (v: number) => bottom - ((bottom - top) * v) / max;
  const active = hover !== null && rows[hover] ? hover : null;
  const headerValue =
    series.length === 1 ? series[0].values[active ?? rows.length - 1] : null;
  const Icon = metric.icon;
  const ticks = [
    ...new Set(
      [0, 1, 2, 3].map((i) => Math.round(((rows.length - 1) * i) / 3)),
    ),
  ];
  return (
    <section className="panel performance-card">
      <div className="performance-card-head">
        <h4 title={metric.hint}>
          <Icon size={16} aria-hidden="true" />
          {metric.title}
        </h4>
        <span
          title={
            daily
              ? "所选日期，默认显示最后一天"
              : "所选小时，默认显示最后一小时"
          }
        >
          {headerValue != null && <b>{display(headerValue, metric.key)} </b>}
          {metric.unit}
        </span>
      </div>
      {samples.length ? (
        <div className="performance-plot">
          <svg
            viewBox="0 0 316 154"
            role="group"
            tabIndex={0}
            aria-label={`${metric.title}逐${daily ? "日" : "小时"}趋势，使用左右方向键查看数值`}
            onFocus={() => setHover(rows.length - 1)}
            onBlur={() => setHover(null)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setHover(null);
              if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
              e.preventDefault();
              setHover(
                Math.min(
                  rows.length - 1,
                  Math.max(
                    0,
                    (active ?? rows.length - 1) +
                      (e.key === "ArrowLeft" ? -1 : 1),
                  ),
                ),
              );
            }}
            onPointerMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const svgX = ((e.clientX - rect.left) * 316) / rect.width;
              setHover(
                Math.min(
                  rows.length - 1,
                  Math.max(
                    0,
                    Math.round(
                      ((svgX - left) / (right - left)) * (rows.length - 1),
                    ),
                  ),
                ),
              );
            }}
            onPointerLeave={() => setHover(null)}
          >
            {[0, 0.5, 1].map((f) => (
              <g key={f}>
                <line
                  x1={left}
                  x2={right}
                  y1={y(f * max)}
                  y2={y(f * max)}
                  className="performance-gridline"
                />
                <text x={left - 6} y={y(f * max) + 3} textAnchor="end">
                  {formatNumber(f * max, {
                    notation: "compact",
                    maximumSignificantDigits: 3,
                  })}
                </text>
              </g>
            ))}
            {ticks.map((i, index) => (
              <text
                key={i}
                x={x(i)}
                y={149}
                textAnchor={
                  index === 0
                    ? "start"
                    : index === ticks.length - 1
                      ? "end"
                      : "middle"
                }
              >
                {(daily || rows.length > 48 ? dayLabel : hourLabel)().format(
                  new Date(rows[i].hour),
                )}
              </text>
            ))}
            {series.map((s, index) => (
              <g key={s.id}>
                <defs>
                  <linearGradient id={id + index} x1="0" y1="0" x2="0" y2="1">
                    <stop stopColor={s.color} stopOpacity=".22" />
                    <stop
                      offset="100%"
                      stopColor={s.color}
                      stopOpacity=".025"
                    />
                  </linearGradient>
                </defs>
                {segments(s.values, x, y).map((run) => {
                  const path = smoothLine(run);
                  if (run.length === 1)
                    return (
                      <circle
                        key={run[0].x}
                        cx={run[0].x}
                        cy={run[0].y}
                        r={2.5}
                        fill={s.color}
                      />
                    );
                  return (
                    <g key={run[0].x}>
                      <path
                        d={
                          path +
                          " L " +
                          run[run.length - 1].x +
                          "," +
                          bottom +
                          " L " +
                          run[0].x +
                          "," +
                          bottom +
                          " Z"
                        }
                        fill={`url(#${id + index})`}
                      />
                      <path
                        d={path}
                        fill="none"
                        stroke={s.color}
                        strokeWidth={2}
                        strokeLinecap="round"
                        vectorEffect="non-scaling-stroke"
                      />
                    </g>
                  );
                })}
              </g>
            ))}
            {active !== null && (
              <line
                x1={x(active)}
                x2={x(active)}
                y1={top}
                y2={bottom}
                className="performance-cursor"
              />
            )}
          </svg>
          {active !== null && (
            <div className="performance-tooltip" role="status">
              <strong>{formatBucketTime(rows[active].hour, daily)}</strong>
              {series.map((s) => (
                <span key={s.id}>
                  <i style={{ background: s.color }} />
                  {s.label}
                  <b>
                    {s.values[active] === null
                      ? "—"
                      : display(s.values[active]!, metric.key) +
                        " " +
                        metric.unit}
                  </b>
                </span>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="performance-empty">
          {metric.key === "error_rate"
            ? "此区间暂无调用样本"
            : metric.key === "tps"
              ? "此区间暂无生成速度样本"
              : "此区间暂无首字延迟样本"}
        </div>
      )}
    </section>
  );
}

export function ModelPerformanceCharts({
  rows,
  models: catalog,
  daily,
}: {
  rows: TokenBucket[];
  models: Map<string, ChartModel>;
  daily: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const present = new Set(
    rows.flatMap((row) => Object.keys(row.model_performance ?? {})),
  );
  const models = [...catalog.values()].filter((model) => present.has(model.id));
  const focused = models.find((model) => model.id === selected);
  return (
    <section className="model-performance" aria-label="模型调用表现">
      <div className="performance-head">
        <h3>模型调用表现</h3>
        <div className="chart-legend" aria-label="筛选模型">
          {models.map((model) => (
            <button
              type="button"
              key={model.id}
              aria-pressed={focused?.id === model.id}
              title={
                focused?.id === model.id
                  ? "显示全部模型"
                  : "仅查看 " + model.label
              }
              className={
                "chart-legend-button" +
                (focused?.id === model.id
                  ? " is-selected"
                  : focused
                    ? " is-dimmed"
                    : "")
              }
              onClick={() =>
                setSelected(focused?.id === model.id ? null : model.id)
              }
            >
              <i style={{ background: model.color }} />
              {model.label}
            </button>
          ))}
        </div>
      </div>
      <div className="performance-grid">
        {metrics.map((metric) => (
          <PerformanceCard
            key={metric.key}
            metric={metric}
            rows={rows}
            models={focused ? [focused] : models}
            daily={daily}
          />
        ))}
      </div>
    </section>
  );
}
