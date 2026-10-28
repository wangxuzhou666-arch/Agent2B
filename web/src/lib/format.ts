/** Display formatting. Values are never rounded beyond what the unit implies. */

/**
 * Step durations, as DESIGN 10.3 specifies: `< 1 s` → "412 ms", `< 10 s` → "3.4 s", else
 * "12 s". Returns null for 0 / missing timings so callers omit them (no false precision).
 */
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms <= 0) return null;
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms / 1000)} s`;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** `YYYY-MM-DD HH:mm` in the viewer's time zone (24 h), or null for an unparseable value. */
export function formatDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** `YYYY-MM-DD HH:mm:ss` for audit-style timestamps. */
export function formatTimestamp(iso: string | null | undefined): string | null {
  const minutes = formatDateTime(iso);
  if (!minutes || !iso) return minutes;
  return `${minutes}:${pad(new Date(iso).getSeconds())}`;
}

/** Countdown clock: `m:ss`, or `h:mm:ss` from one hour up. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

const numberFormats = new Map<string, Intl.NumberFormat>();

/** Locale grouping for a computed number; keeps every fraction digit the tool produced. */
export function formatNumber(value: number, locale: string): string {
  let format = numberFormats.get(locale);
  if (!format) {
    format = new Intl.NumberFormat(locale, { maximumFractionDigits: 20 });
    numberFormats.set(locale, format);
  }
  return format.format(value);
}

/** "on_hand_qty" → "On hand qty"; well-known short codes stay upper case. */
export function humanizeKey(key: string): string {
  const words = key.split(/[_\s]+/).filter(Boolean);
  return words
    .map((word, i) => {
      if (/^(id|sku|po|eta|uom)$/i.test(word)) return word.toUpperCase();
      return i === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word;
    })
    .join(" ");
}

/** Initial for an avatar fallback. */
export function initialOf(name: string): string {
  return (name.trim().charAt(0) || "?").toUpperCase();
}
