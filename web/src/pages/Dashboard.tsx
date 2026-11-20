import { ModelPerformanceCharts } from "../components/ModelPerformanceCharts";
import { modelColor, smoothLine, type ChartModel } from "../lib/charts";
import { configuredModelName, modelName } from "../lib/models";
import { LoadingBar } from "../components/LoadingBar";
import { MetricIcon } from "../components/MetricIcon";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type {
  Currency,
  ModelUsage,
  Statistics,
  TokenBucket,
} from "../api/types";
import { formatHour, formatNumber } from "../lib/utils";

const CURRENCY_SYMBOL: Record<Currency, string> = { USD: "$", CNY: "¥" };
const CURRENCY_STORAGE_KEY = "shiritaibot.cost-currency";

function savedCurrency(): Currency {
  try {
    return localStorage.getItem(CURRENCY_STORAGE_KEY) === "CNY" ? "CNY" : "USD";
  } catch {
    return "USD";
  }
}

function formatCost(value: number, currency: Currency): string {
  const digits = value > 0 && value < 0.01 ? 4 : 2;
  return CURRENCY_SYMBOL[currency] + value.toFixed(digits);
}

function formatRate(value: number | null): string {
  return value === null ? "—" : (value * 100).toFixed(1) + "%";
}

function convertedCost(
  value: number,
  source: Currency,
  target: Currency,
  rate: number | null,
): string {
  if (source === target || rate === null) return formatCost(value, source);
  return formatCost(target === "CNY" ? value * rate : value / rate, target);
}

function totalCost(
  costs: Statistics["costs"],
  currency: Currency,
  rate: number | null,
): string {
  if (rate !== null && Object.keys(costs).length) {
    const total =
      currency === "CNY"
        ? (costs.CNY ?? 0) + (costs.USD ?? 0) * rate
        : (costs.USD ?? 0) + (costs.CNY ?? 0) / rate;
    return formatCost(total, currency);
  }
  const parts = (Object.keys(CURRENCY_SYMBOL) as Currency[])
    .filter((currency) => costs[currency] !== undefined)
    .map((currency) => formatCost(costs[currency]!, currency));
  return parts.length ? parts.join(" + ") : "—";
}

function ModelTable({
  rows,
  currency,
  rate,
  models,
}: {
  rows: ModelUsage[];
  currency: Currency;
  rate: number | null;
  models: Map<string, ChartModel>;
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>模型用量</h3>
      </div>
      {rows.length ? (
        <div className="table-scroll">
          <table className="usage-table">
            <thead>
              <tr>
                <th scope="col">模型</th>
                <th scope="col">调用</th>
                <th scope="col">输入 tokens</th>
                <th scope="col">缓存命中</th>
                <th scope="col">命中率</th>
                <th scope="col">输出 tokens</th>
                <th scope="col">预估花费</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.model}>
                  <th scope="row">{models.get(row.model)?.label}</th>
                  <td>{formatNumber(row.llm_calls)}</td>
                  <td>{formatNumber(row.input_tokens)}</td>
                  <td>{formatNumber(row.cached_tokens)}</td>
                  <td>
                    <span className="rate">
                      <i
                        aria-hidden="true"
                        style={{
                          width: `${(row.cache_hit_rate ?? 0) * 100}%`,
                        }}
                      />
                    </span>
                    {formatRate(row.cache_hit_rate)}
                  </td>
                  <td>{formatNumber(row.output_tokens)}</td>
                  <td>
                    {row.cost !== null && row.currency ? (
                      convertedCost(row.cost, row.currency, currency, rate)
                    ) : (
                      <span
                        className="muted"
                        title="在「模型」页填写或获取单价"
                      >
                        未设单价
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">此区间内没有模型调用。</p>
      )}
    </section>
  );
}

const HOUR_RANGES = [
  [24, "24 小时"],
  [72, "3 天"],
  [168, "7 天"],
] as const;

interface Series {
  key: string;
  label: string;
  color: string;
  value: (row: TokenBucket) => number;
}

const OTHER_COLOR = "#9aa3b2";

function modelSeries(
  rows: TokenBucket[],
  models: Map<string, ChartModel>,
): Series[] {
  const present = new Set(rows.flatMap((row) => Object.keys(row.models ?? {})));
  const visible = [...models.values()].filter((model) => present.has(model.id));
  const maxSeries = 8;
  const own =
    visible.length > maxSeries ? visible.slice(0, maxSeries - 1) : visible;
  const rest = visible.slice(own.length);
  const series: Series[] = own.map(({ id, label, color }) => ({
    key: "model:" + id,
    label,
    color,
    value: (row) => row.models?.[id] ?? 0,
  }));
  if (rest.length)
    series.push({
      key: "other",
      label: `其他（${rest.length} 个）`,
      color: OTHER_COLOR,
      value: (row) =>
        rest.reduce((sum, model) => sum + (row.models?.[model.id] ?? 0), 0),
    });
  return series;
}

function field(key: keyof Statistics["totals"]) {
  return (row: TokenBucket) => row[key];
}

function HourlyChart({
  title,
  rows,
  series,
}: {
  title: string;
  rows: TokenBucket[];
  series: Series[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [focusedSeries, setFocusedSeries] = useState<string | null>(null);
  const [logScale, setLogScale] = useState(false);
  const [logMix, setLogMix] = useState(0);
  const logMixRef = useRef(0);

  useEffect(() => {
    const target = logScale ? 1 : 0;
    const from = logMixRef.current;
    if (from === target) return;

    if (
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      logMixRef.current = target;
      setLogMix(target);
      return;
    }

    const duration = 320;
    const start = performance.now();
    let frameId = 0;

    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased =
        progress < 0.5
          ? 4 * progress * progress * progress
          : 1 - Math.pow(-2 * progress + 2, 3) / 2;
      const next = from + (target - from) * eased;
      logMixRef.current = next;
      setLogMix(next);
      if (progress < 1) {
        frameId = requestAnimationFrame(tick);
      }
    };

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [logScale]);

  const highlighted = series.some((item) => item.key === focusedSeries)
    ? focusedSeries
    : null;
  const lineSeries = [
    ...series.filter((item) => item.key !== highlighted),
    ...series.filter((item) => item.key === highlighted),
  ];
  const seriesClass = (key: string) =>
    `chart-series${highlighted !== null && highlighted !== key ? " is-dimmed" : ""}`;
  const maximum = Math.max(
    1,
    ...rows.map((row) => Math.max(0, ...series.map((item) => item.value(row)))),
  );
  const width = 900;
  const height = 280;
  const labelHeight = 28;
  const logMax = Math.log10(1 + maximum);
  const scaleY = (v: number) => {
    if (v <= 0) return 0;
    const linear = (v / maximum) * height;
    if (logMix === 0) return linear;
    const log = (Math.log10(1 + v) / logMax) * height;
    if (logMix === 1) return log;
    return linear + (log - linear) * logMix;
  };
  const step = width / Math.max(1, rows.length);
  const selected = hover === null ? null : rows[hover];

  return (
    <section className="panel chart-panel">
      <div className="panel-head">
        <h3>{title}</h3>
        <div className="chart-controls">
          <div className="chart-legend">
            {series.map((item) => (
              <button
                type="button"
                key={item.key}
                className={`chart-legend-button${highlighted === item.key ? " is-selected" : ""}${highlighted !== null && highlighted !== item.key ? " is-dimmed" : ""}`}
                aria-pressed={highlighted === item.key}
                title={
                  highlighted === item.key
                    ? "再次点击显示全部曲线"
                    : `高亮 ${item.label}`
                }
                onClick={() =>
                  setFocusedSeries(highlighted === item.key ? null : item.key)
                }
              >
                <i style={{ background: item.color }} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className={`chart-scale-toggle${logScale ? " is-selected" : ""}`}
            aria-pressed={logScale}
            title={logScale ? "切换为线性尺度" : "切换为对数尺度 (log10)"}
            onClick={() => setLogScale((prev) => !prev)}
          >
            对数尺度
          </button>
        </div>
      </div>
      <div className="chart-plot">
        <div
          className="chart-scale"
          aria-hidden="true"
          style={{ bottom: `${(labelHeight / (height + labelHeight)) * 100}%` }}
        >
          <span>{formatNumber(maximum)}</span>
          <span>0</span>
        </div>
        <svg
          className="hourly-chart"
          viewBox={`0 0 ${width} ${height + labelHeight}`}
          role="group"
          aria-label={title + "逐小时图表"}
          onMouseLeave={() => setHover(null)}
        >
          {[0, 0.5, 1].map((level) => (
            <line
              key={level}
              x1={0}
              y1={level * height}
              x2={width}
              y2={level * height}
              stroke="currentColor"
              opacity=".12"
            />
          ))}
          {rows.map((row, index) => (
            <g
              key={row.hour}
              tabIndex={0}
              role="img"
              aria-label={`${formatHour(row.hour)} ${series
                .map((item) => `${item.label} ${item.value(row)}`)
                .join("，")}`}
              onMouseEnter={() => setHover(index)}
              onFocus={() => setHover(index)}
              onBlur={() => setHover(null)}
            >
              <rect
                x={index * step}
                y={0}
                width={step}
                height={height}
                fill={index === hover ? "var(--accent-soft)" : "transparent"}
              />
              {series.map((item) => (
                <circle
                  key={item.key}
                  className={seriesClass(item.key)}
                  cx={(index + 0.5) * step}
                  cy={height - scaleY(item.value(row))}
                  r={index === hover ? 4 : rows.length === 1 ? 3 : 0}
                  fill={item.color}
                  stroke="var(--panel)"
                  strokeWidth={1}
                  pointerEvents="none"
                />
              ))}
              {(index === 0 ||
                index === rows.length - 1 ||
                index % Math.ceil(rows.length / 5) === 0) && (
                <text
                  x={index * step + step / 2}
                  y={height + 22}
                  textAnchor={
                    index === 0
                      ? "start"
                      : index === rows.length - 1
                        ? "end"
                        : "middle"
                  }
                  fill="currentColor"
                  fontSize={12}
                >
                  {formatHour(row.hour)}
                </text>
              )}
            </g>
          ))}
          {lineSeries.map((item) => (
            <path
              key={item.key}
              className={seriesClass(item.key)}
              d={smoothLine(
                rows.map((row, index) => ({
                  x: (index + 0.5) * step,
                  y: height - scaleY(item.value(row)),
                })),
              )}
              fill="none"
              stroke={item.color}
              strokeWidth={
                highlighted === null || highlighted === item.key ? 3 : 2
              }
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              pointerEvents="none"
            />
          ))}
        </svg>
      </div>
      <div className="chart-detail" aria-live="polite">
        {selected ? (
          <>
            <strong>{formatHour(selected.hour)}</strong>
            {series
              .filter((item) => item.value(selected) > 0)
              .map((item) => (
                <span key={item.key} className={seriesClass(item.key)}>
                  <i style={{ background: item.color }} aria-hidden="true" />
                  {item.label} {formatNumber(item.value(selected))}
                </span>
              ))}
          </>
        ) : (
          <span className="muted">
            默认高亮全部曲线；点击图例突出某条曲线，再次点击恢复全部。悬停或用
            Tab 查看每小时数据。
          </span>
        )}
      </div>
    </section>
  );
}

export function Dashboard({ client }: { client: ApiClient }) {
  const [hours, setHours] = useState<number>(24);
  const [currency, setCurrency] = useState<Currency>(savedCurrency);
  const exchange = useQuery({
    queryKey: ["exchange-rate"],
    queryFn: () => client.exchangeRate(),
    staleTime: 300000,
    refetchInterval: 300000,
    retry: false,
  });
  const rate = exchange.data?.usd_cny ?? null;
  const toggleCurrency = () => {
    const next = currency === "USD" ? "CNY" : "USD";
    setCurrency(next);
    try {
      localStorage.setItem(CURRENCY_STORAGE_KEY, next);
    } catch {
      // Currency selection still works when browser storage is unavailable.
    }
  };
  const query = useQuery({
    queryKey: ["stats", hours],
    queryFn: () => client.stats(hours),
    refetchInterval: 30000,
  });
  const data: Statistics | undefined = query.data;
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => client.config(),
  });
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => client.diagnostics(),
    staleTime: 60000,
    refetchInterval: 60000,
    retry: false,
  });
  const warnings = diagnostics.data?.warnings ?? [];
  const configured = config.data?.models ?? [];
  const recorded = (data?.buckets ?? []).flatMap((row) => [
    ...Object.keys(row.models ?? {}),
    ...Object.keys(row.model_performance ?? {}),
  ]);
  const models = new Map<string, ChartModel>();
  configured.forEach((model, index) => {
    models.set(model.id, {
      id: model.id,
      label: configuredModelName(model),
      color: modelColor(model.id, index),
    });
  });
  const recordedLabels = new Map(
    (data?.models ?? []).map((row) => [row.model, row.model_label]),
  );
  for (const id of [
    ...new Set([...recorded, ...(data?.models ?? []).map((row) => row.model)]),
  ].sort()) {
    if (!models.has(id))
      models.set(id, {
        id,
        label: recordedLabels.get(id) || modelName("", id),
        color: modelColor(id, -1),
      });
  }

  return (
    <section className="dashboard">
      <div className="panel-head">
        <div>
          <h2>数据看板</h2>
          <p className="muted">北京时间 · 每小时聚合 · 每 30 秒更新</p>
        </div>
        <div className="range-tabs" role="group" aria-label="统计区间">
          {HOUR_RANGES.map(([value, label]) => (
            <button
              type="button"
              key={value}
              className={hours === value ? "selected" : ""}
              aria-pressed={hours === value}
              onClick={() => setHours(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {query.error && (
        <QueryErrorNotice message={`读取统计失败：${query.error.message}`} />
      )}
      {warnings.length > 0 && (
        <section className="panel" role="alert">
          <div className="panel-head">
            <h3>配置需要检查</h3>
          </div>
          <ul className="muted">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}
      {data && data.totals && data.buckets ? (
        <>
          <div className="metrics metrics-wide">
            {(
              [
                ["总 tokens", formatNumber(data.totals.total_tokens)],
                [
                  "缓存命中率",
                  formatRate(
                    data.totals.input_tokens
                      ? data.totals.cached_tokens / data.totals.input_tokens
                      : null,
                  ),
                ],
                ["预估花费", totalCost(data.costs ?? {}, currency, rate)],
                ["对话消息", formatNumber(data.totals.messages)],
                ["模型调用", formatNumber(data.totals.llm_calls)],
                ["失败轮次", formatNumber(data.totals.failed_turns)],
              ] as const
            ).map(([label, value]) => (
              <div
                className={`metric${label === "预估花费" ? " cost-metric" : ""}`}
                key={label}
              >
                <MetricIcon label={label} />
                <span>{label}</span>
                <strong>{value}</strong>
                {label === "预估花费" && (
                  <small className="cost-exchange-rate" role="status">
                    {rate !== null ? (
                      <>
                        <span
                          title={
                            exchange.data?.updated_at
                              ? `汇率更新于 ${new Date(exchange.data.updated_at * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}（北京时间）`
                              : undefined
                          }
                        >
                          1 USD ≈ {rate.toFixed(4)} CNY
                          {exchange.data?.stale || exchange.error
                            ? "（缓存汇率，更新暂不可用）"
                            : ""}
                        </span>
                        {" · "}
                        <a
                          href="https://www.exchangerate-api.com"
                          target="_blank"
                          rel="noreferrer"
                        >
                          Rates By Exchange Rate API
                        </a>
                      </>
                    ) : exchange.isPending ? (
                      <LoadingBar
                        compact
                        label="正在获取汇率，暂按原币种显示"
                      />
                    ) : (
                      "汇率暂不可用，按原币种显示"
                    )}
                  </small>
                )}
                <div className="metric-footer">
                  <small>
                    过去 {hours === 24 ? "24 小时" : `${hours / 24} 天`}
                  </small>
                  {label === "预估花费" && (
                    <button
                      type="button"
                      className="currency-toggle"
                      onClick={toggleCurrency}
                      aria-label={`切换为${currency === "USD" ? "人民币" : "美元"}花费`}
                      title={`当前选择${currency === "USD" ? "美元" : "人民币"}，点击切换`}
                    >
                      {currency === "USD" ? "$ USD" : "¥ CNY"}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          <ModelPerformanceCharts rows={data.buckets} models={models} />
          <HourlyChart
            title="Token 用量（按模型）"
            rows={data.buckets}
            series={[
              {
                key: "total",
                label: "总用量",
                color: "var(--text)",
                value: field("total_tokens"),
              },
              ...modelSeries(data.buckets, models),
            ]}
          />
          <ModelTable
            rows={data.models ?? []}
            models={models}
            currency={currency}
            rate={rate}
          />
          <HourlyChart
            title="对话消息"
            rows={data.buckets}
            series={[
              {
                key: "incoming_messages",
                label: "用户消息",
                color: "var(--chart-incoming)",
                value: field("incoming_messages"),
              },
              {
                key: "outgoing_messages",
                label: "助手回复",
                color: "var(--chart-outgoing)",
                value: field("outgoing_messages"),
              },
            ]}
          />
          <p className="muted stats-note">
            统计区间 {formatHour(data.start)} 至 {formatHour(data.end)}
            ，首尾小时按实际区间截取。Token
            取提供商返回的用量，包含工具轮次与连接测试； 缓存命中{" "}
            {formatNumber(data.totals.cached_tokens)} tokens（已计入输入）。
            花费按模型当前单价估算，仅供参考：缓存写入按输入单价计，不含分级定价与免费额度；
            已删除或未设单价的模型不计入。
            消息统计已入库的用户消息和最终回复，包含斜杠命令，不含工具结果与内部交接；
            未返回用量的请求不估算 tokens。
          </p>
        </>
      ) : (
        !query.error && <LoadingBar label="正在读取统计…" />
      )}
    </section>
  );
}
