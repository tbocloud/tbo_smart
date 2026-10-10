/**
 * The prototype runs on a fixed clock so every screen, fixture and screenshot
 * agrees on "today" (Sat 10 Oct 2026, 10:42 AM IST). Swap for `new Date()`
 * once the API is real.
 */
const FIXED_NOW = new Date(2026, 9, 10, 10, 42, 0);

export function now(): Date {
  return new Date(FIXED_NOW.getTime());
}

/** Local datetime as "YYYY-MM-DDTHH:mm" relative to the fixed clock. */
export function at(dayOffset: number, hhmm: string): string {
  const d = now();
  d.setDate(d.getDate() + dayOffset);
  return `${isoDate(d)}T${hhmm}`;
}

export function isoDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function day(dayOffset: number): string {
  const d = now();
  d.setDate(d.getDate() + dayOffset);
  return isoDate(d);
}
