"use client";

import { useQueryClient } from "@tanstack/react-query";
import { BellRing } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";

import { Toast, ToastStack } from "@/components/ui/Toast";
import { useRouter } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";
import { REPORTS_PATH, reportSearch } from "@/lib/automations";
import { useReportStream, type ReportEvent } from "@/lib/use-report-stream";

import { REPORTS_KEY } from "./ReportsBrowser";
import { STATS_KEY } from "./ProfileStats";
import { UNREAD_KEY, UNREAD_LIST_KEY } from "./ReportNotifications";

/**
 * Says a report arrived, wherever the user happens to be.
 *
 * Mounted once, in `VisionApp`, beside `SelectionReply` and for the same reason:
 * this is a property of the dashboard rather than of any page in it. The whole
 * point is that it fires while the user is doing something else — reading the FX
 * board, mid-conversation with the assistant — so it cannot live on the profile
 * page, which is the one place they would have found out anyway.
 *
 * **Two effects per report, and they are different jobs.** The toast is the
 * interruption: here is a thing, here is the way to it, and it leaves on its
 * own. The bell is the record: it keeps the count until the report is opened.
 * A toast that is missed therefore costs nothing, which is what lets it
 * auto-dismiss without a confirmation.
 *
 * The caches are invalidated rather than written to. The socket's message
 * carries enough to draw the toast, but the badge's count is a server-side
 * `COUNT` over unread rows and inventing `count + 1` here would drift the first
 * time two tabs are open or a report is read on a phone.
 */

/** How long a toast stays. Long enough to read a title and decide, and no
 *  longer: it covers the page it is announcing over. */
const DISMISS_MS = 9_000;

/** How many stack before the oldest is pushed out, so a burst of overdue
 *  automations cannot paper over the whole viewport. */
const MAX_VISIBLE = 3;

export function ReportToasts() {
  const t = useTranslations("nav");
  const tc = useTranslations("common");
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [toasts, setToasts] = useState<ReportEvent[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    setToasts((current) => current.filter((report) => report.id !== id));
  }, []);

  const onReport = useCallback(
    (report: ReportEvent) => {
      // The badge, the menu's list, the Reports tab and the profile counter all
      // just became stale. Invalidating is cheap — each is one indexed query —
      // and it keeps the server's numbers authoritative.
      queryClient.invalidateQueries({ queryKey: UNREAD_KEY });
      queryClient.invalidateQueries({ queryKey: UNREAD_LIST_KEY });
      queryClient.invalidateQueries({ queryKey: REPORTS_KEY });
      queryClient.invalidateQueries({ queryKey: STATS_KEY });

      setToasts((current) => {
        // A reconnect can replay nothing, but a double-delivered frame is
        // cheaper to guard than to reason about.
        if (current.some((existing) => existing.id === report.id)) return current;
        return [...current, report].slice(-MAX_VISIBLE);
      });
      timers.current.set(
        report.id,
        setTimeout(() => dismiss(report.id), DISMISS_MS),
      );
    },
    [dismiss, queryClient],
  );

  useReportStream(Boolean(user), onReport);

  // Every pending timer, on unmount. Without this a navigation away mid-toast
  // leaves a `setState` scheduled against a component that is gone.
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((timer) => clearTimeout(timer));
      pending.clear();
    };
  }, []);

  if (toasts.length === 0) return null;

  return (
    <ToastStack>
      {toasts.map((report) => (
        <Toast
          key={report.id}
          icon={<BellRing size={16} />}
          title={t("newReport")}
          body={report.title}
          openLabel={t("newReportOpen")}
          dismissLabel={tc("dismiss")}
          onOpen={() => {
            dismiss(report.id);
            // The same deep link the bell's menu uses, so a report opened from
            // here and one opened from there land on the identical URL.
            const search = reportSearch("", report.id);
            router.push(`${REPORTS_PATH}${search ? `?${search}` : ""}`);
          }}
          onDismiss={() => dismiss(report.id)}
        />
      ))}
    </ToastStack>
  );
}
