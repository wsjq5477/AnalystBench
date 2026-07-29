export function formatDurationMs(value) {
  if (value === null || value === undefined || value === "") return "—";
  const durationMs = Number(value);
  if (!Number.isFinite(durationMs) || durationMs < 0) return "—";
  if (durationMs < 10000) return `${(durationMs / 1000).toFixed(1)} 秒`;
  const totalSeconds = Math.round(durationMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes} 分 ${String(seconds).padStart(2, "0")} 秒`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours} 时 ${String(remainingMinutes).padStart(2, "0")} 分`;
}

export function elapsedDurationMs(startedAt, nowMs = Date.now()) {
  if (!startedAt) return null;
  const startedMs = new Date(startedAt).getTime();
  if (!Number.isFinite(startedMs)) return null;
  return Math.max(0, nowMs - startedMs);
}
