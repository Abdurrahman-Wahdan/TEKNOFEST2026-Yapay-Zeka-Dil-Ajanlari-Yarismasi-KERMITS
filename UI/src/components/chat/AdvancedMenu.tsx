"use client";

import { useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import { api, type ChatModel } from "@/lib/api";

/**
 * The composer's Advanced menu: which model answers, and whether it thinks.
 *
 * It replaces the "Think" chip rather than sitting beside it. Thinking was one
 * of two settings that belong together and only one of them had a control, so
 * the row grew a toggle for the cheap setting and had nowhere to put the
 * expensive one. Both live here now, and the chip says "Advanced".
 *
 * The model list is fetched, never hardcoded. `GET /api/models` derives it from
 * the specs measured against the running vLLM host, so a model added there
 * appears here without a frontend change -- the same reason the context window
 * is read from the server rather than pinned in a constant.
 */

/**
 * Model names are proper nouns and do not translate, so they are not i18n keys.
 *
 * Written out rather than built from `key` because an unknown model must still
 * render: it falls through to the id vLLM serves it as, which is factual and
 * always present, instead of a blank row or a missing-message error.
 */
function modelName(model: ChatModel): string {
  if (model.key === "gemma") return "Gemma";
  if (model.key === "qwen") return "Qwen";
  if (model.key === "gpt") return "GPT";
  return model.model_id;
}

export function AdvancedMenu({
  think,
  model,
  onThink,
  onModel,
}: {
  think: boolean;
  /** Undefined means "whatever the server's default is". */
  model?: string;
  onThink: (on: boolean) => void;
  onModel: (key: string | undefined) => void;
}) {
  const t = useTranslations("chat");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["chatModels"],
    queryFn: api.models,
    // The served models change when the host restarts, not between renders.
    staleTime: 5 * 60 * 1000,
  });

  const models = data?.models ?? [];
  const activeKey = model ?? data?.default;
  const active = models.find((entry) => entry.key === activeKey);

  // The switch is only real for models that reason by default; for the rest the
  // backend discards the flag. Showing it enabled would rebuild exactly the
  // dead control this menu exists to replace.
  const thinkable = active?.supports_thinking ?? false;

  /**
   * Explicit per-model lookups, not `t(`modelHint.${key}`)`.
   *
   * next-intl validates and hot-reloads static nested keys; a runtime-built one
   * can hold a stale namespace through a Turbopack update and throw on a key
   * that is perfectly valid. Same reason the comparator spells its groups out.
   */
  function modelHint(entry: ChatModel): string {
    if (entry.key === "gemma") return t("modelHint.gemma");
    if (entry.key === "qwen") return t("modelHint.qwen");
    if (entry.key === "gpt") return t("modelHint.gpt");
    return entry.model_id;
  }

  return (
    <VuiBox
      role="dialog"
      aria-label={t("advanced")}
      sx={{
        position: "absolute",
        right: 0,
        // Above the composer. The mention list opens downward because it belongs
        // to text being typed; this belongs to a button on the bottom row, and
        // the composer sits at the bottom of the viewport.
        bottom: "calc(100% + 8px)",
        zIndex: 3,
        width: 288,
        maxHeight: 360,
        overflowY: "auto",
        // Same shell as MentionMenu, so the two popups read as one family.
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--popover)",
        border: "1px solid var(--border)",
        boxShadow: "0 8px 24px rgb(0 0 0 / 0.18)",
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        p: 1,
      }}
    >
      <GroupLabel>{t("advancedModel")}</GroupLabel>

      {isLoading && <Note>{t("modelsLoading")}</Note>}
      {isError && <Note>{t("modelsError")}</Note>}

      {/* The rows are `role="option"`, which is only valid inside a listbox --
          left directly under the dialog they are options belonging to nothing,
          and a screen reader announces neither the set nor the position in it. */}
      <VuiBox
        role="listbox"
        aria-label={t("advancedModel")}
        display="flex"
        flexDirection="column"
        gap={0.5}
      >
      {models.map((entry) => {
        const selected = entry.key === activeKey;
        return (
          <VuiBox
            key={entry.key}
            component="button"
            type="button"
            role="option"
            aria-selected={selected}
            aria-label={modelName(entry)}
            onClick={(event: React.MouseEvent) => {
              event.stopPropagation();
              // The server's default is sent as null rather than its key, so a
              // deployment that changes CHAT_MODEL moves this user with it.
              onModel(entry.key === data?.default ? undefined : entry.key);
            }}
            display="flex"
            alignItems="center"
            gap={1}
            sx={{
              width: "100%",
              textAlign: "left",
              border: "none",
              cursor: "pointer",
              px: 1.25,
              py: 1,
              borderRadius: "15px",
              backgroundColor: selected ? "var(--muted)" : "transparent",
              color: "var(--foreground)",
              fontFamily: "inherit",
              "&:hover": { backgroundColor: "var(--muted)" },
              "&:focus-visible": {
                outline: "2px solid var(--ring)",
                outlineOffset: -2,
              },
            }}
          >
            {/* Plain spans, not VuiBox: VuiBox paints its own `color` and would
                override the row's, which is what made the Think chip render a
                different shade from the controls beside it. */}
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: "block", fontSize: "0.875rem", fontWeight: 500 }}>
                {modelName(entry)}
              </span>
              <span
                style={{
                  display: "block",
                  fontSize: "0.75rem",
                  color: "var(--control-ink)",
                }}
              >
                {modelHint(entry)}
              </span>
            </span>
            <span
              style={{
                display: "flex",
                flexShrink: 0,
                visibility: selected ? "visible" : "hidden",
                color: "var(--primary-strong)",
              }}
            >
              <Check size={16} />
            </span>
          </VuiBox>
        );
      })}
      </VuiBox>

      <VuiBox
        sx={{ height: "1px", backgroundColor: "var(--border)", mx: 1.25, my: 0.5 }}
      />

      <GroupLabel>{t("advancedThinking")}</GroupLabel>

      <VuiBox
        component="button"
        type="button"
        role="switch"
        aria-checked={thinkable && think}
        disabled={!thinkable}
        onClick={(event: React.MouseEvent) => {
          event.stopPropagation();
          onThink(!think);
        }}
        display="flex"
        alignItems="center"
        gap={1}
        sx={{
          width: "100%",
          textAlign: "left",
          border: "none",
          cursor: thinkable ? "pointer" : "not-allowed",
          px: 1.25,
          py: 1,
          borderRadius: "15px",
          backgroundColor: "transparent",
          color: "var(--foreground)",
          fontFamily: "inherit",
          opacity: thinkable ? 1 : 0.55,
          "&:hover:not(:disabled)": { backgroundColor: "var(--muted)" },
          "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: -2 },
        }}
      >
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "block", fontSize: "0.875rem", fontWeight: 500 }}>
            {t("think")}
          </span>
          <span
            style={{ display: "block", fontSize: "0.75rem", color: "var(--control-ink)" }}
          >
            {/* Naming the model is the whole point: "unavailable" alone reads as
                broken, while "Gemma does not support this" tells the user the
                fix is the row above. */}
            {thinkable
              ? t("thinkHint")
              : t("thinkUnsupported", { model: active ? modelName(active) : "" })}
          </span>
        </span>
        <Track on={thinkable && think} />
      </VuiBox>
    </VuiBox>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <VuiBox px={1.25} pt={0.5} pb={0.25}>
      <VuiTypography
        variant="caption"
        sx={{
          color: "var(--control-ink)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          fontWeight: "var(--weight-medium)",
        }}
      >
        {children}
      </VuiTypography>
    </VuiBox>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <VuiBox px={1.25} py={1}>
      <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
        {children}
      </VuiTypography>
    </VuiBox>
  );
}

/** The switch itself. Drawn rather than imported: MUI's Switch brings its own
 *  palette and would be the one control in the composer not using our tokens. */
function Track({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden
      style={{
        display: "flex",
        alignItems: "center",
        flexShrink: 0,
        width: 34,
        height: 20,
        padding: 2,
        borderRadius: "var(--radius-full)",
        backgroundColor: on ? "var(--primary)" : "var(--muted)",
        transition: "background-color 150ms ease",
      }}
    >
      <span
        style={{
          width: 16,
          height: 16,
          borderRadius: "var(--radius-full)",
          backgroundColor: on ? "var(--primary-foreground)" : "var(--control-ink)",
          transform: on ? "translateX(14px)" : "translateX(0)",
          transition: "transform 150ms ease, background-color 150ms ease",
        }}
      />
    </span>
  );
}
