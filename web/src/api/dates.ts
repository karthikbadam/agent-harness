/**
 * Server returns ISO timestamps without a `Z` suffix because the underlying
 * SQLite column is timezone-naive. The browser would otherwise parse those as
 * local time, shifting "today" by your offset. Force-treat them as UTC.
 */
export function parseServerDate(iso: string): Date {
  if (/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)) return new Date(iso);
  return new Date(iso + "Z");
}

/** Return a local-timezone "midnight" for the given Date. */
function localMidnight(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/**
 * Compact relative time: "just now", "12m ago", "3h ago", "Yesterday 14:22",
 * "Mon Jun 3", or "May 12 2024" if more than a year ago. Anchored in local
 * time so today/yesterday don't shift across UTC midnight.
 */
export function relativeTime(d: Date, now: Date = new Date()): string {
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  const sameDay = localMidnight(d).getTime() === localMidnight(now).getTime();
  if (sameDay) return `${diffHr}h ago`;
  const yesterday = new Date(localMidnight(now));
  yesterday.setDate(yesterday.getDate() - 1);
  if (localMidnight(d).getTime() === yesterday.getTime()) {
    return `Yesterday ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  });
}
