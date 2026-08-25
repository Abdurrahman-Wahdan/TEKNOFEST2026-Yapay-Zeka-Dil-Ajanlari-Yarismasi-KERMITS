"use client";

import Grid from "@mui/material/Grid";
import Skeleton from "@mui/material/Skeleton";
import { useQuery } from "@tanstack/react-query";
import {
  Bell,
  Bot,
  FileText,
  MessageCircle,
  MessagesSquare,
  Table2,
  TriangleAlert,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { CenteredState } from "@/components/ui/CenteredState";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";

type Locale = "tr" | "en";

/** The one cache key for this. Used here and nowhere else. */
export const STATS_KEY = ["user-stats"];

/**
 * What this user has actually done with the product.
 *
 * Replaces the Vision template's demo cards — a fake car, placeholder platform
 * settings, three projects by people who do not exist. Every number here is a
 * `COUNT` over rows that already existed; nothing new is recorded to produce
 * them.
 *
 * **There is no token count**, and that is a decision rather than an omission.
 * Nothing in this application records model usage: the supervisor asks for
 * `stream_usage` but no handler collects it, and the ten bank specialists' spend
 * is unobserved entirely. A "tokens" tile would read zero for every conversation
 * ever held, which says something false rather than nothing.
 */
export function ProfileStats() {
  const t = useTranslations("profile");
  const locale = useLocale() as Locale;

  const stats = useQuery({ queryKey: STATS_KEY, queryFn: () => api.stats() });

  if (stats.isLoading) {
    return (
      <Grid container spacing={3}>
        {[0, 1, 2, 3].map((n) => (
          <Grid item xs={6} md={3} key={n}>
            <Skeleton variant="rounded" height={96} />
          </Grid>
        ))}
      </Grid>
    );
  }

  if (stats.isError || !stats.data) {
    return (
      <CenteredState
        icon={<TriangleAlert size={22} />}
        label={t("statsFailed")}
        tone="error"
      />
    );
  }

  const s = stats.data;
  const tiles: { key: string; label: string; value: number; icon: ReactNode }[] = [
    {
      key: "sessions",
      label: t("chatSessions"),
      value: s.chat_sessions,
      icon: <MessagesSquare size={18} />,
    },
    {
      key: "sent",
      label: t("messagesSent"),
      value: s.messages_sent,
      icon: <MessageCircle size={18} />,
    },
    {
      key: "received",
      label: t("messagesReceived"),
      value: s.messages_received,
      icon: <Bot size={18} />,
    },
    {
      key: "tables",
      label: t("savedTables"),
      value: s.saved_tables,
      icon: <Table2 size={18} />,
    },
    {
      key: "automations",
      label: t("automationCount"),
      value: s.automations,
      icon: <Bell size={18} />,
    },
    {
      key: "reports",
      label: t("reportCount"),
      value: s.reports,
      icon: <FileText size={18} />,
    },
  ];

  return (
    <VuiBox display="flex" flexDirection="column" gap="16px">
      <Grid container spacing={3}>
        {tiles.map((tile) => (
          <Grid item xs={6} md={4} xl={2} key={tile.key}>
            <StatTile
              label={tile.label}
              value={formatNumber(tile.value, locale)}
              icon={tile.icon}
            />
          </Grid>
        ))}
      </Grid>
      {/*
        The two dates sit under the tiles rather than among them: they are the
        only entries that are not a count, and a date in a row of numbers reads
        as a number that failed to render.
      */}
      <VuiBox display="flex" flexWrap="wrap" gap="24px">
        <Caption
          label={t("firstActivity")}
          value={
            s.first_activity ? formatDate(s.first_activity, locale) : t("never")
          }
        />
        <Caption
          label={t("lastActivity")}
          value={
            s.last_activity ? formatDate(s.last_activity, locale) : t("never")
          }
        />
      </VuiBox>
    </VuiBox>
  );
}

function StatTile({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon: ReactNode;
}) {
  return (
    <VuiBox
      display="flex"
      flexDirection="column"
      gap="6px"
      sx={{
        // `--card`, not a Vision palette entry: this tile has to read correctly
        // in both themes, and the template's own surfaces are dark-only.
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "16px",
        padding: "16px",
        height: "100%",
      }}
    >
      <VuiBox
        display="flex"
        alignItems="center"
        gap="8px"
        sx={{ color: "var(--control-ink)" }}
      >
        {icon}
        <VuiTypography
          variant="caption"
          sx={{ color: "var(--control-ink)", lineHeight: 1.3 }}
        >
          {label}
        </VuiTypography>
      </VuiBox>
      <VuiTypography
        variant="h4"
        fontWeight="bold"
        sx={{ color: "var(--foreground)" }}
      >
        {value}
      </VuiTypography>
    </VuiBox>
  );
}

function Caption({ label, value }: { label: string; value: string }) {
  return (
    <VuiBox display="flex" gap="8px" alignItems="baseline">
      <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
        {label}
      </VuiTypography>
      <VuiTypography variant="caption" sx={{ color: "var(--foreground)" }}>
        {value}
      </VuiTypography>
    </VuiBox>
  );
}
