/**
 * Addressing one report in the URL, and reading a schedule out loud.
 *
 * `/tr/profile/reports?rapor=<id>` opens that report instead of the list — which
 * is what the notification bell links to, so a report has an address a user can
 * bookmark, reload and share with themselves.
 *
 * The same shape as `table-url.ts`, deliberately: this app already had one way
 * to put an item's identity in the query string, and a second convention would
 * mean two things to keep in step. Turkish, like the rest of this data domain
 * (`tablo`, `Banka`, `Kaynak`).
 */
export const REPORT_PARAM = "rapor";

/** Where the Reports tab lives. One constant, because three files link to it. */
export const REPORTS_PATH = "/profile/reports";

/**
 * The search string for a given selection — `"rapor=..."`, or `""` when nothing
 * is open. Other parameters already on the URL are preserved.
 */
export function reportSearch(
  current: URLSearchParams | string,
  reportId: string | null,
): string {
  const params = new URLSearchParams(current);
  if (reportId) {
    params.set(REPORT_PARAM, reportId);
  } else {
    params.delete(REPORT_PARAM);
  }
  return params.toString();
}

/** The in-app href for one report, locale included. */
export function reportHref(locale: string, reportId: string | null): string {
  const search = reportSearch("", reportId);
  return `/${locale}${REPORTS_PATH}${search ? `?${search}` : ""}`;
}

/**
 * `datetime.weekday()`: Monday is 0 and Sunday is 6.
 *
 * Not `Date.getDay()`, which puts Sunday at 0 — the storage side is Python and
 * the two disagree by one, which is exactly the kind of difference that shows up
 * as a report arriving on the wrong day. Convert at the boundary with
 * `fromJsDay`, never by remembering to add one at each call site.
 */
export const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;
export type Weekday = (typeof WEEKDAYS)[number];

/** A JavaScript `getDay()` value as a Python `weekday()` value. */
export function fromJsDay(day: number): Weekday {
  return ((day + 6) % 7) as Weekday;
}

/** Monday to Friday, the set "hafta içi" means. */
export const WEEKDAYS_ONLY: Weekday[] = [0, 1, 2, 3, 4];
/** Saturday and Sunday. */
export const WEEKEND: Weekday[] = [5, 6];

/** `9` and `0` as `"09:00"`. */
export function formatTime(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

/**
 * Whether two weekday sets mean the same schedule.
 *
 * Both sides store them sorted and deduplicated (`_clean_weekdays` in
 * `api/schemas/automations.py`, `valid_weekdays` in
 * `api/automations/schedule.py`), so comparing in order is safe — but only
 * because of that, which is why this is a named function rather than an inline
 * `join()` at three call sites.
 */
export function sameDays(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((day, index) => day === b[index]);
}

/** What `describeSchedule` needs from the translation catalogue. */
export type ScheduleLabels = {
  /** "Her gün {time}" */
  daily: (time: string) => string;
  /** "Hafta içi {time}" */
  weekdays: (time: string) => string;
  /** "Hafta sonu {time}" */
  weekend: (time: string) => string;
  /** "{days} {time}" */
  someDays: (days: string, time: string) => string;
  /** Short day names, Monday first. */
  dayNames: readonly string[];
};

/**
 * A schedule as one line: "Her gün 09:00", "Hafta içi 07:30", "Pzt, Cum 21:30".
 *
 * The named sets come first because they are how people describe the schedule
 * they actually chose — someone who picked Monday through Friday reads "Hafta
 * içi" as their own words, and "Pzt, Sal, Çar, Per, Cum" as a list they now have
 * to parse back into that idea.
 *
 * `api/automations/schedule.py::describe` writes the same line for server logs.
 * The two are allowed to differ in wording — this one has the translation
 * catalogue and that one must be readable in a log with no catalogue at all —
 * but never in *meaning*, so a schedule never reads one way on screen and
 * another in the logs.
 */
export function describeSchedule(
  hour: number,
  minute: number,
  days: readonly number[],
  labels: ScheduleLabels,
): string {
  const time = formatTime(hour, minute);
  if (days.length === 0) return labels.daily(time);
  if (sameDays(days, WEEKDAYS_ONLY)) return labels.weekdays(time);
  if (sameDays(days, WEEKEND)) return labels.weekend(time);
  const names = days
    .filter((day) => day >= 0 && day <= 6)
    .map((day) => labels.dayNames[day])
    .join(", ");
  return labels.someDays(names, time);
}
