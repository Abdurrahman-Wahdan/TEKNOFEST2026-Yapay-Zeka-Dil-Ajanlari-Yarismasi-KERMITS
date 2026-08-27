"use client";

import Card from "@mui/material/Card";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Trash2, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { CenteredState } from "@/components/ui/CenteredState";
import { VuiBox, VuiButton, VuiTypography } from "@/components/vision";
import { api, type SavedView } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { layout } from "@/lib/layout";
import { savedViewSpecs, savedViewTitle } from "@/lib/saved-view";

import { spanRuleFor } from "./catalog";
import { RenderComponent } from "./renderComponent";

type Locale = "tr" | "en";

/** The one cache key for this list. Used here and nowhere else. */
const KEY = ["saved-views"];

/**
 * The tables the agent saved for this user.
 *
 * Every table on this page was written by the assistant, either because the user
 * asked for one or because they pressed save on one it had already produced. So
 * the page has no controls of its own beyond delete: what it shows is a
 * consequence of the conversation, not of anything configured here.
 *
 * Each saved view holds a single `{type: "table", props}`, which goes through
 * `RenderComponent` — the same path a produced topic-page table takes, and the only
 * place the component contract is enforced. A view the agent wrote badly therefore
 * appears as a specific, visible complaint rather than a blank card.
 */
export function SavedViewsBoard() {
  const t = useTranslations("aiOverview");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const queryClient = useQueryClient();
  const [failed, setFailed] = useState<string | null>(null);

  const views = useQuery({ queryKey: KEY, queryFn: () => api.views() });

  const remove = useMutation({
    mutationFn: (slug: string) => api.deleteView(slug),
    onError: (_error, slug) => setFailed(slug),
    // `onSettled`, not `onSuccess`: a slug already gone answers 404, and the list
    // still needs refreshing — that is exactly the case where it is stale.
    onSettled: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });

  if (views.isLoading) {
    return (
      <VuiBox display="flex" flexDirection="column" gap="24px">
        {[0, 1].map((n) => (
          <Skeleton key={n} variant="rounded" height={320} />
        ))}
      </VuiBox>
    );
  }

  if (views.isError) {
    return (
      <Card>
        <CenteredState icon={<TriangleAlert size={22} />} label={t("loadFailed")} tone="error">
          <VuiButton size="small" variant="outlined" color="white" onClick={() => views.refetch()}>
            {tc("retry")}
          </VuiButton>
        </CenteredState>
      </Card>
    );
  }

  const rows = views.data ?? [];
  if (rows.length === 0) {
    return (
      <Card>
        <CenteredState icon={<Sparkles size={22} />} label={t("empty")} />
      </Card>
    );
  }

  // A table's span rule is `{preferred: 4}`, so tables come out full width, one to
  // a row. Reading the rule rather than hardcoding it means a future non-table
  // saved component lays itself out correctly with no change here.
  const spans = layout(rows.map((view) => spanRuleFor(view.components?.[0]?.type ?? "table")));

  return (
    <Grid container spacing={3}>
      {rows.map((view, index) => (
        <Grid key={view.slug} item xs={12} xl={(spans[index]?.span ?? 4) * 3}>
          <SavedViewCard
            view={view}
            locale={locale}
            deleting={remove.isPending && remove.variables === view.slug}
            failed={failed === view.slug}
            onDelete={() => {
              setFailed(null);
              remove.mutate(view.slug);
            }}
          />
        </Grid>
      ))}
    </Grid>
  );
}

function SavedViewCard({
  view,
  locale,
  deleting,
  failed,
  onDelete,
}: {
  view: SavedView;
  locale: Locale;
  deleting: boolean;
  failed: boolean;
  onDelete: () => void;
}) {
  const t = useTranslations("aiOverview");
  const specs = savedViewSpecs(view);
  const description = specs.find((spec) => spec.type === "table")?.props;
  const subtitle =
    typeof description === "object" && description !== null &&
    typeof (description as { subtitle?: unknown }).subtitle === "string"
      ? (description as { subtitle: string }).subtitle
      : "";

  return (
    <Card sx={{ height: "100%", opacity: deleting ? 0.5 : 1 }}>
      <VuiBox
        display="flex"
        alignItems="flex-start"
        justifyContent="space-between"
        gap="12px"
        mb={2}
      >
        {/* A flex column: VuiTypography renders inline, so without this the date
            runs straight into the end of the title. */}
        <VuiBox display="flex" flexDirection="column">
          {/* Rendered raw. The agent wrote this string; `t()` would throw on it. */}
          <VuiTypography variant="lg" color="white" fontWeight="bold">
            {savedViewTitle(view, t("untitled"))}
          </VuiTypography>
          {subtitle && (
            <VuiTypography variant="caption" color="text" mt={0.5}>
              {subtitle}
            </VuiTypography>
          )}
          <VuiTypography variant="caption" color="text">
            {t("savedAt", { date: formatDate(view.updated_at, locale) })}
          </VuiTypography>
        </VuiBox>
        <Tooltip title={failed ? t("deleteFailed") : t("delete")}>
          <IconButton
            size="small"
            aria-label={t("delete")}
            disabled={deleting}
            onClick={onDelete}
            sx={{ color: failed ? "error.main" : "text.main" }}
          >
            <Trash2 size={16} />
          </IconButton>
        </Tooltip>
      </VuiBox>

      {specs.map((spec, index) => (
        <RenderComponent key={`${spec.type}-${index}`} spec={spec} />
      ))}
    </Card>
  );
}
