/**
 * Server returns ISO timestamps without a `Z` suffix because the underlying
 * SQLite column is timezone-naive. The browser would otherwise parse those as
 * local time, shifting "today" by your offset. Force-treat them as UTC.
 */
export function parseServerDate(iso: string): Date {
  if (/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)) return new Date(iso);
  return new Date(iso + "Z");
}
