import { en } from "@/i18n/en";
import type { Tone } from "@/theme/tokens";

import { isoDate, now } from "./clock";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Parses "YYYY-MM-DD" or "YYYY-MM-DDTHH:mm" as local time (no UTC shift). */
export function parse(value: string): Date {
  const [datePart, timePart = "00:00"] = value.split("T");
  const [y, m, d] = datePart.split("-").map(Number);
  const [hh, mm] = timePart.split(":").map(Number);
  return new Date(y, m - 1, d, hh, mm);
}

export function time(value: string | Date): string {
  const d = typeof value === "string" ? parse(value) : value;
  const h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, "0");
  const suffix = h >= 12 ? "PM" : "AM";
  return `${h % 12 === 0 ? 12 : h % 12}:${m} ${suffix}`;
}

/** "9:30" style shift time from "09:30". */
export function shiftTime(hhmm: string): string {
  return time(`2000-01-01T${hhmm}`);
}

/** "Thu 8 Oct" */
export function dayLabel(value: string | Date): string {
  const d = typeof value === "string" ? parse(value) : value;
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** "8 Oct 2026" */
export function longDate(value: string): string {
  const d = parse(value);
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export function monthLabel(year: number, month: number): string {
  return `${MONTHS_LONG[month]} ${year}`;
}

export function shortMonth(month: number): string {
  return MONTHS[month];
}

export function weekdayShort(index: number): string {
  return WEEKDAYS[index];
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function daysFromToday(value: string): number {
  return Math.round((startOfDay(parse(value)) - startOfDay(now())) / 86_400_000);
}

/** "10:42 AM" today, "Yesterday", or "Thu 8 Oct" for notification timestamps. */
export function relativeStamp(value: string): string {
  const diff = daysFromToday(value);
  if (diff === 0) return time(value);
  if (diff === -1) return en.time.yesterdayTitle;
  return dayLabel(value);
}

export type DueInfo = { text: string; tone: Tone; overdue: boolean };

/** Task due label with its meaning: overdue is danger, today/soon is info. */
export function dueInfo(value: string): DueInfo {
  const diff = daysFromToday(value);
  if (diff < 0 || (diff === 0 && parse(value) < now())) {
    const since = diff === -1 ? en.time.yesterday : diff === 0 ? time(value) : dayLabel(value);
    return { text: en.time.overdueSince(since), tone: "danger", overdue: true };
  }
  if (diff === 0) return { text: en.time.dueToday(time(value)), tone: "info", overdue: false };
  if (diff === 1) return { text: en.time.dueTomorrow(time(value)), tone: "info", overdue: false };
  return { text: en.time.dueOn(dayLabel(value)), tone: "neutral", overdue: false };
}

/** SLA countdown: "2h left", "45m left", "1d left" or "Overdue 3h". */
export function slaInfo(value: string): DueInfo {
  const ms = parse(value).getTime() - now().getTime();
  const abs = Math.abs(ms);
  const mins = Math.round(abs / 60_000);
  const span = mins < 60 ? `${mins}m` : mins < 60 * 24 ? `${Math.round(mins / 60)}h` : `${Math.round(mins / 1440)}d`;
  if (ms < 0) return { text: en.time.slaOverdue(span), tone: "danger", overdue: true };
  return { text: en.time.slaLeft(span), tone: mins <= 240 ? "warning" : "neutral", overdue: false };
}

/** Indian digit grouping without relying on Intl (Hermes builds vary). */
export function rupees(amount: number): string {
  const [whole, frac] = amount.toFixed(2).split(".");
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  const grouped = rest ? `${rest},${last3}` : last3;
  return `₹${grouped}${frac === "00" ? "" : `.${frac}`}`;
}

export function hours(value: number): string {
  return `${value.toFixed(1).replace(/\.0$/, "")}h`;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const second = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + second).toUpperCase();
}

/** Monday-first calendar grid for a month, padded with nulls. */
export function monthGrid(year: number, month: number): (string | null)[] {
  const first = new Date(year, month, 1);
  const lead = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (string | null)[] = Array.from({ length: lead }, () => null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(isoDate(new Date(year, month, d)));
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

export function dayCount(from: string, to: string, halfDay: boolean): number {
  const span = Math.round((parse(to).getTime() - parse(from).getTime()) / 86_400_000) + 1;
  if (span < 1) return 0;
  return halfDay && span === 1 ? 0.5 : span;
}

/** Newest first, for merging notifications from both sites. */
export function byNewest(a: { at: string }, b: { at: string }): number {
  return parse(b.at).getTime() - parse(a.at).getTime();
}
