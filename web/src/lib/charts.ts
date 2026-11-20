export interface ChartModel {
  id: string;
  label: string;
  color: string;
}

const palette = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#ae7600",
  "#c45384",
  "#008300",
  "#6250d6",
  "#e34948",
];

/** Configured slots stay fixed as series appear and disappear. Historical IDs have no slot. */
export function modelColor(id: string, slot: number) {
  if (slot >= 0)
    return palette[slot] ?? `hsl(${(slot * 137.508) % 360} 62% 43%)`;
  let hash = 2166136261;
  for (let i = 0; i < id.length; i++)
    hash = Math.imul(hash ^ id.charCodeAt(i), 16777619);
  const hue = (hash >>> 0) % 360;
  return `hsl(${hue} 65% ${hue >= 45 && hue < 195 ? 36 : 48}%)`;
}

/** Monotone cubic interpolation keeps curves within each pair of hourly values. */
export function smoothLine(points: { x: number; y: number }[]): string {
  if (!points.length) return "";
  const slopes = points.slice(1).map((point, index) => {
    const previous = points[index];
    return (point.y - previous.y) / (point.x - previous.x);
  });
  const tangents = points.map((_, index) => {
    if (index === 0) return slopes[0] ?? 0;
    if (index === points.length - 1) return slopes[index - 1];
    const left = slopes[index - 1];
    const right = slopes[index];
    return left * right <= 0 ? 0 : (2 * left * right) / (left + right);
  });
  return points.reduce((path, point, index) => {
    if (index === 0) return `M ${point.x},${point.y}`;
    const previous = points[index - 1];
    const third = (point.x - previous.x) / 3;
    return `${path} C ${previous.x + third},${previous.y + tangents[index - 1] * third} ${point.x - third},${point.y - tangents[index] * third} ${point.x},${point.y}`;
  }, "");
}
