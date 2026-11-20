export const MODEL_COLORS = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#6250d6",
  "#e34948",
];

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
