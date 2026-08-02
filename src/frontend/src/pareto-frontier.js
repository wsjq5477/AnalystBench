export function paretoFrontier(points) {
  let bestScore = -Infinity;
  return points
    .map((point) => (Array.isArray(point) ? point : point?.value))
    .filter(
      (value) =>
        Array.isArray(value) &&
        Number.isFinite(Number(value[0])) &&
        Number(value[0]) > 0 &&
        Number.isFinite(Number(value[1])),
    )
    .map((value) => [Number(value[0]), Number(value[1])])
    .sort(
      (left, right) =>
        left[0] - right[0] ||
        right[1] - left[1],
    )
    .filter((value) => {
      if (value[1] <= bestScore) return false;
      bestScore = value[1];
      return true;
    });
}
