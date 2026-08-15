"use client";

import Card from "@mui/material/Card";
import Grid from "@mui/material/Grid";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { VuiBox, VuiButton, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";
import {
  ComponentsResponseSchema,
  MAX_COMPONENTS,
  type ComponentSpec,
} from "@/lib/contract";
import { layout } from "@/lib/layout";

import { spanRuleFor } from "./catalog";
import { RenderComponent } from "./renderComponent";

/**
 * Everything a producer made for one topic page.
 *
 * The producer sends an ordered list of components. Tables are the common case
 * and get a switcher — several tables about one subject are alternative views
 * of it, not a wall to scroll — while anything else is laid out in the grid by
 * width rules we own.
 *
 * What this deliberately does not do is trust the payload. The envelope is
 * re-validated here even though it came from our own API, because the API
 * forwards produced JSON without checking it; and every individual component
 * goes through `renderComponent`, which turns a bad one into a visible account
 * of what went wrong rather than a gap.
 */
export function CategoryComponents({ category }: { category: string }) {
  const t = useTranslations("components");
  const tc = useTranslations("common");

  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ["components", category],
    queryFn: () => api.categoryComponents(category),
    staleTime: 5 * 60 * 1000,
  });

  const parsed = useMemo(
    () => (data ? ComponentsResponseSchema.safeParse(data) : null),
    [data],
  );

  const all = parsed?.success ? parsed.data.components : [];
  const shown = all.slice(0, MAX_COMPONENTS);
  const overflow = all.length - shown.length;

  const tables = shown.filter((c) => c.type === "table");
  const others = shown.filter((c) => c.type !== "table");

  const [activeIndex, setActiveIndex] = useState(0);
  // The producer can change under us (a refetch, a new category), so the index
  // is clamped at read time rather than trusted from state.
  const safeIndex = Math.min(activeIndex, Math.max(tables.length - 1, 0));
  const active = tables[safeIndex];

  // Not memoized by hand: `others` is rebuilt each render, so a manual dep
  // array here defeats the React Compiler rather than helping it — and laying
  // out at most eight items is nothing. The compiler memoizes this itself.
  const spans = layout(others.map((c) => spanRuleFor(c.type)));

  if (isPending) {
    return <Notice>{tc("loading")}</Notice>;
  }

  if (isError) {
    return (
      <Notice>
        {tc("error")}{" "}
        <VuiTypography
          component="button"
          variant="button"
          color="info"
          onClick={() => refetch()}
          sx={{ background: "none", border: "none", cursor: "pointer", textDecoration: "underline" }}
        >
          {tc("retry")}
        </VuiTypography>
      </Notice>
    );
  }

  // Our own API answered with something this build cannot read. Rare, and worth
  // saying plainly rather than rendering an empty page that looks finished.
  if (parsed && !parsed.success) return <Notice>{t("malformed")}</Notice>;
  if (all.length === 0) return <Notice>{t("noComponents")}</Notice>;

  return (
    <VuiBox display="flex" flexDirection="column" gap="24px">
      {parsed?.success && parsed.data.source === "fixture" && (
        // Placeholder content must never be mistakeable for bank data.
        <VuiBox
          px={2}
          py={1.25}
          borderRadius="lg"
          sx={{ border: "1px dashed", borderColor: "warning.main", background: "rgba(255,255,255,0.03)" }}
        >
          <VuiTypography variant="caption" color="warning">
            {t("fixtureNotice")}
          </VuiTypography>
        </VuiBox>
      )}

      {active && (
        <Card>
          {/* Title on its own line, switcher beneath it. Side by side, four
              long Turkish table names squeezed the heading into a narrow
              column and still overflowed; stacked, both get the full width. */}
          <VuiBox mb="22px">
            <VuiTypography variant="lg" color="white">
              {tableTitle(active, t("untitledTable"))}
            </VuiTypography>

            {tables.length > 1 && (
              <VuiBox mt={2} display="flex" flexWrap="wrap" gap="8px">
                {tables.map((table, index) => (
                  <VuiButton
                    key={tableKey(table, index)}
                    size="small"
                    variant={index === safeIndex ? "contained" : "outlined"}
                    // "white", not "light": on this dark surface the `light`
                    // palette entry is near-invisible as an outline.
                    color={index === safeIndex ? "info" : "white"}
                    onClick={() => setActiveIndex(index)}
                  >
                    {tableTitle(table, t("untitledTable"))}
                  </VuiButton>
                ))}
              </VuiBox>
            )}
          </VuiBox>

          {/* Keyed so switching tables remounts: filter state from a table with
              a bank column must not silently hide rows in one without. */}
          <VuiBox key={tableKey(active, safeIndex)}>
            <RenderComponent spec={active} />
          </VuiBox>
        </Card>
      )}

      {others.length > 0 && (
        <Grid container spacing={3}>
          {others.map((component, index) => (
            <Grid key={`${component.type}-${index}`} item xs={12} xl={(spans[index]?.span ?? 2) * 3}>
              <Card sx={{ height: "100%" }}><RenderComponent spec={component} /></Card>
            </Grid>
          ))}
        </Grid>
      )}

      {overflow > 0 && <Notice>{t("overflow", { count: overflow })}</Notice>}
    </VuiBox>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <Card>
      <VuiTypography variant="button" color="text" fontWeight="regular">
        {children}
      </VuiTypography>
    </Card>
  );
}

/**
 * A table's title, read defensively.
 *
 * `props` is unvalidated at this point — the switcher has to label a table
 * whose props may be the very thing that fails validation, so it reads the
 * title without assuming anything about the rest.
 *
 * Rendered raw, never through `t()`: it is the producer's string, and passing
 * it to next-intl would throw the moment it is not a translation key.
 */
function tableTitle(spec: ComponentSpec, fallback: string): string {
  const title = (spec.props as { title?: unknown } | null)?.title;
  return typeof title === "string" && title.trim() !== "" ? title : fallback;
}

function tableKey(spec: ComponentSpec, index: number): string {
  const id = (spec.props as { id?: unknown } | null)?.id;
  return typeof id === "string" && id !== "" ? id : `table-${index}`;
}
