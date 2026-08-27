"use client";

import Skeleton from "@mui/material/Skeleton";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import {
  ArrowLeft,
  ChevronRight,
  FileText,
  TriangleAlert,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { AttachButton } from "@/components/chat/AttachButton";
import { CenteredState } from "@/components/ui/CenteredState";
import { Pill } from "@/components/ui/Pill";
import { VuiBox, VuiButton, VuiTypography } from "@/components/vision";
import { usePathname } from "@/i18n/navigation";
import { api, type AutomationReportSummary } from "@/lib/api";
import { REPORT_PARAM, reportSearch } from "@/lib/automations";
import { useChat } from "@/lib/chat/ChatProvider";
import { elideLabel } from "@/lib/chat/context-format";
import { formatDateTime } from "@/lib/format";

import { UNREAD_KEY } from "./ReportNotifications";
import { STATS_KEY } from "./ProfileStats";

type Locale = "tr" | "en";

/** The one cache key for the report list. */
export const REPORTS_KEY = ["automation-reports"];

/**
 * A report is an assistant answer, so it is rendered by the assistant's
 * renderer. Dynamically imported with `ssr: false` exactly as `ChatMessage`
 * does — Shiki is large, and this page is not the chat.
 */
const AgentMarkdown = dynamic(
  () => import("@/components/chat/AgentMarkdown").then((m) => m.AgentMarkdown),
  { ssr: false },
);

/**
 * The Reports tab: the list, and one open report.
 *
 * Two states of one component rather than two routes, following
 * `CompareTablesBrowser` — including how the open report gets an address.
 * `initialReportId` comes from the page's `searchParams`, so a link from the
 * notification bell is correct in the first paint and needs no Suspense
 * boundary; `pushState` keeps the URL in step afterwards.
 */
export function ReportsBrowser({
  initialReportId = null,
}: {
  initialReportId?: string | null;
}) {
  const t = useTranslations("reports");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const [reportId, setReportId] = useState<string | null>(initialReportId);

  /**
   * Open a report, or go back to the list, and put that in the URL.
   *
   * `pushState` and not `router.push`: these are two states of this component,
   * not two routes. Next hooks the native history methods, so its router still
   * sees the change — and pushing rather than replacing is what makes Back close
   * the report, which is what Back means here.
   */
  const select = (id: string | null) => {
    setReportId(id);
    const search = reportSearch(window.location.search, id);
    window.history.pushState(
      null,
      "",
      search ? `?${search}` : window.location.pathname,
    );
  };

  useEffect(() => {
    const onPopState = () => {
      setReportId(new URLSearchParams(window.location.search).get(REPORT_PARAM));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  if (reportId) {
    return <OneReport id={reportId} locale={locale} onBack={() => select(null)} />;
  }
  return <ReportList locale={locale} onOpen={select} t={t} tc={tc} />;
}

function ReportList({
  locale,
  onOpen,
  t,
  tc,
}: {
  locale: Locale;
  onOpen: (id: string) => void;
  t: ReturnType<typeof useTranslations<"reports">>;
  tc: ReturnType<typeof useTranslations<"common">>;
}) {
  const reports = useQuery({
    queryKey: REPORTS_KEY,
    queryFn: () => api.automationReports(),
  });

  if (reports.isLoading) {
    return (
      <VuiBox display="flex" flexDirection="column" gap="10px">
        {[0, 1, 2].map((n) => (
          <Skeleton key={n} variant="rounded" height={64} />
        ))}
      </VuiBox>
    );
  }

  if (reports.isError) {
    return (
      <CenteredState
        icon={<TriangleAlert size={22} />}
        label={t("loadFailed")}
        tone="error"
      >
        <VuiButton
          size="small"
          variant="outlined"
          color="white"
          onClick={() => reports.refetch()}
        >
          {tc("retry")}
        </VuiButton>
      </CenteredState>
    );
  }

  const rows = reports.data ?? [];
  if (rows.length === 0) {
    return <CenteredState icon={<FileText size={22} />} label={t("empty")} />;
  }

  return (
    <VuiBox display="flex" flexDirection="column" gap="10px">
      {rows.map((row) => (
        <ReportRow key={row.id} row={row} locale={locale} onOpen={onOpen} t={t} />
      ))}
    </VuiBox>
  );
}

function ReportRow({
  row,
  locale,
  onOpen,
  t,
}: {
  row: AutomationReportSummary;
  locale: Locale;
  onOpen: (id: string) => void;
  t: ReturnType<typeof useTranslations<"reports">>;
}) {
  const unread = row.read_at === null;
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={() => onOpen(row.id)}
      display="flex"
      alignItems="center"
      gap="12px"
      sx={{
        width: "100%",
        textAlign: "start",
        cursor: "pointer",
        background: "var(--card)",
        // An unread report earns the accent border. The row is otherwise
        // identical read or unread, so the notification and the list agree about
        // what "new" means without a second visual language.
        border: `1px solid ${unread ? "var(--primary)" : "var(--border)"}`,
        borderRadius: "14px",
        padding: "12px 16px",
        fontFamily: "inherit",
      }}
    >
      <FileText
        size={18}
        style={{ color: unread ? "var(--primary-strong)" : "var(--control-ink)" }}
      />
      <VuiBox flex={1} display="flex" flexDirection="column" gap="2px">
        <VuiBox display="flex" alignItems="center" gap="8px" flexWrap="wrap">
          <VuiTypography
            variant="button"
            fontWeight={unread ? "bold" : "regular"}
            sx={{ color: "var(--foreground)" }}
          >
            {row.title}
          </VuiTypography>
          {unread && <Pill tone="ok">{t("unread")}</Pill>}
          {row.status === "failed" && <Pill tone="bad">{t("failed")}</Pill>}
          {row.automation_id === null && (
            <Pill tone="neutral">{t("deletedAutomation")}</Pill>
          )}
        </VuiBox>
        <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
          {formatDateTime(row.created_at, locale)}
        </VuiTypography>
      </VuiBox>
      <ChevronRight size={16} style={{ color: "var(--control-ink)" }} />
    </VuiBox>
  );
}

function OneReport({
  id,
  locale,
  onBack,
}: {
  id: string;
  locale: Locale;
  onBack: () => void;
}) {
  const t = useTranslations("reports");
  const tc = useTranslations("common");
  // The assistant's own namespace: the button names an assistant action, and
  // those strings live together -- the same rule `ProducedTable` follows.
  const tChat = useTranslations("chat");
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const { attachments, setPopupOpen } = useChat();

  const report = useQuery({
    queryKey: [...REPORTS_KEY, id],
    queryFn: () => api.automationReport(id),
  });

  /**
   * Opening is what clears the notification, and it is a separate call from
   * fetching on purpose: a retry or a cache revalidation must not silently clear
   * a badge for a report the user never saw.
   */
  const markRead = useMutation({
    mutationFn: () => api.markReportRead(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: UNREAD_KEY });
      queryClient.invalidateQueries({ queryKey: REPORTS_KEY });
      queryClient.invalidateQueries({ queryKey: STATS_KEY });
    },
  });

  const loaded = report.data;
  const alreadyRead = loaded?.read_at !== null && loaded?.read_at !== undefined;
  const pending = markRead.isPending || markRead.isSuccess;
  useEffect(() => {
    // Once, and only for a report that actually loaded and is actually unread.
    if (loaded && !alreadyRead && !pending) markRead.mutate();
    // `markRead` is a stable mutation object; the guard above is what makes this
    // fire once rather than on every render of a report still marked unread in
    // the cache.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded?.id, alreadyRead, pending]);

  /**
   * Hand the whole report to the assistant.
   *
   * The report's markdown verbatim -- it *is* an assistant answer, so it needs no
   * reserialising, and nothing is cut: the tables inside it are the part a reader
   * has follow-up questions about. Attaching the body rather than reading the
   * rendered DOM back out is the same choice `useAttachTable` documents: the call
   * site has the exact source in hand.
   */
  const attachReport = () => {
    if (!loaded?.body) return;
    attachments.addContext({
      kind: "report",
      label: elideLabel(loaded.title),
      body: loaded.body,
      format: "markdown",
      location: {
        path: pathname,
        // The page, not the report's own title -- the chip's label is already the
        // title, and a subline repeating it says nothing twice. The date rides
        // along as `about` so the agent knows how current the figures are without
        // asking.
        page: t("title"),
        about: tChat("reportAbout", {
          date: formatDateTime(loaded.created_at, locale),
        }),
      },
    });
    // Open the panel unless the user is already looking at the conversation.
    if (pathname !== "/chat") setPopupOpen(true);
  };

  return (
    <VuiBox display="flex" flexDirection="column" gap="16px">
      <VuiBox>
        <VuiButton
          size="small"
          variant="text"
          color="white"
          onClick={onBack}
          sx={{ display: "flex", alignItems: "center", gap: "6px" }}
        >
          <ArrowLeft size={16} />
          {t("back")}
        </VuiButton>
      </VuiBox>

      {report.isLoading && <Skeleton variant="rounded" height={240} />}

      {report.isError && (
        <CenteredState
          icon={<TriangleAlert size={22} />}
          label={t("notFound")}
          tone="error"
        >
          <VuiButton
            size="small"
            variant="outlined"
            color="white"
            onClick={() => report.refetch()}
          >
            {tc("retry")}
          </VuiButton>
        </CenteredState>
      )}

      {loaded && (
        <VuiBox
          display="flex"
          flexDirection="column"
          gap="12px"
          sx={{
            background: "var(--card)",
            border: "1px solid var(--border)",
            borderRadius: "20px",
            padding: "20px",
          }}
        >
          <VuiBox display="flex" alignItems="flex-start" gap="12px">
            <VuiBox flex={1} display="flex" flexDirection="column" gap="4px">
              <VuiTypography
                variant="h5"
                fontWeight="bold"
                sx={{ color: "var(--foreground)" }}
              >
                {loaded.title}
              </VuiTypography>
              <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
                {formatDateTime(loaded.created_at, locale)}
              </VuiTypography>
            </VuiBox>
            {/* Only when there is something to hand over: an empty or failed run
                has no body, and a button that stages nothing is a broken one. */}
            {loaded.body && (
              <AttachButton
                label={tChat("attachReport")}
                onClick={attachReport}
                alwaysVisible
              />
            )}
          </VuiBox>

          {/* A failed run still has a report, and it says why. Silence would be
              indistinguishable from an automation the user forgot they made. */}
          {loaded.status === "failed" && (
            <VuiTypography variant="caption" sx={{ color: "var(--destructive)" }}>
              {t("failedDetail", { error: loaded.error })}
            </VuiTypography>
          )}

          {loaded.body ? (
            <AgentMarkdown>{loaded.body}</AgentMarkdown>
          ) : (
            <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
              {t("emptyBody")}
            </VuiTypography>
          )}
        </VuiBox>
      )}
    </VuiBox>
  );
}
