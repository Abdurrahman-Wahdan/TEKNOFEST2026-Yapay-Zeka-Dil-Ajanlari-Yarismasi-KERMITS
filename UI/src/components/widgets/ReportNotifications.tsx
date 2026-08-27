"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";

import { api } from "@/lib/api";

/** The one cache key for the badge. Invalidated when a report is marked read. */
export const UNREAD_KEY = ["automation-reports", "unread-count"];

/** The one cache key for the menu's list, exported for the same reason:
 *  `ReportToasts` invalidates both the moment the socket announces a report,
 *  and a second copy of either array would silently stop matching. */
export const UNREAD_LIST_KEY = ["automation-reports", "unread"];

/**
 * How often the bell asks whether a report has arrived.
 *
 * A minute. Reports are produced by a schedule with minute granularity and a run
 * takes minutes, so a tighter poll would only add lines to the server log — the
 * same reasoning `TableOverview` uses for its own poll. The request is one
 * indexed `COUNT` (`ix_automation_reports_unread`), which is why it can run on a
 * timer in every open tab at all.
 */
const POLL_MS = 60_000;

/** How many unread reports the bell lists before it stops naming them. */
export const MENU_LIMIT = 5;

/**
 * The unread count for the notification badge.
 *
 * A hook rather than a component because it has two consumers that render
 * nothing alike: the navbar's icon button and the drawer's labelled row. See
 * `NotificationsMenu`.
 */
export function useUnreadReportCount(): number {
  const query = useQuery({
    queryKey: UNREAD_KEY,
    queryFn: () => api.unreadReportCount(),
    refetchInterval: POLL_MS,
    // Not in a background tab: a user who left this open for the weekend does
    // not need 2,880 counts, and the first foreground refetch is immediate.
    refetchIntervalInBackground: false,
    // A signed-out user 401s here, and `lib/query.tsx` does not retry that —
    // so a failure is simply no badge, which is the correct thing to show.
    retry: false,
  });
  return query.data?.unread ?? 0;
}

/** The unread reports themselves, for the menu. */
export function useUnreadReports() {
  const locale = useLocale();
  const t = useTranslations("nav");
  const query = useQuery({
    queryKey: UNREAD_LIST_KEY,
    queryFn: () => api.automationReports(true),
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    retry: false,
  });
  return {
    locale,
    t,
    reports: (query.data ?? []).slice(0, MENU_LIMIT),
    isError: query.isError,
    isLoading: query.isLoading,
  };
}
