"use client";

import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { Download } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useState, type ReactNode } from "react";

import { ToggleChip } from "@/components/ui/ToggleChip";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";
import {
  filenameFrom,
  saveBlob,
  type ExportFormat,
  type ExportRequest,
} from "@/lib/export";

/** Which rows go in the file. */
export type ExportScope = "view" | "full";

type Counts = { view: number; full: number };

type Props = {
  open: boolean;
  onClose: () => void;
  /** The formats this source can become. A report cannot become a CSV. */
  formats: readonly ExportFormat[];
  /** The request body for a chosen format and scope. */
  request: (format: ExportFormat, scope: ExportScope) => ExportRequest;
  /**
   * Row counts for the two scopes, or null where there is only one — a report,
   * or a table nobody has filtered.
   */
  scope?: Counts | null;
  /** Used only if the server sends no `Content-Disposition`. */
  fallbackName: string;
};

/**
 * Choosing a format, and getting the file.
 *
 * A dialog rather than a four-item dropdown because there is a second decision
 * to make. A table on screen has usually been filtered and sorted, and "the 12
 * rows I am looking at" and "all 204" are both legitimate answers — put in a
 * menu they read as an afterthought under the formats, and a menu that has to
 * explain itself is a dialog that has not admitted it yet.
 *
 * **The scope control is absent, not disabled, when the two are the same.**
 * An unfiltered table offers a choice between 204 rows and 204 rows, which is a
 * control that lies about having an effect.
 *
 * Built on MUI's `Dialog` with the app's CSS variables, following
 * `chat/FeedbackDialog.tsx` — the same shape, so the two do not drift into two
 * different-looking modals.
 *
 * **Split in two, and the split is what resets it.** MUI unmounts a closed
 * Dialog's children, so every choice made last time — the format, the scope, a
 * failure that was showing — lives in `Body` and is gone by the next open with
 * no effect to reset it. Only `working` sits out here, because the backdrop and
 * the escape key have to be refused mid-download and `onClose` is this
 * component's.
 */
export function ExportDialog({ open, onClose, ...rest }: Props) {
  const [working, setWorking] = useState(false);

  const close = useCallback(() => {
    if (working) return;
    onClose();
  }, [onClose, working]);

  return (
    <Dialog
      open={open}
      onClose={close}
      // The chat popup treats an overlay it does not own as a click-away and
      // closes itself; this marks the overlay as one of ours, exactly as
      // `FeedbackDialog` does.
      className="tf26-agent-popup-owned-overlay"
      fullWidth
      maxWidth="xs"
      PaperProps={{
        sx: {
          borderRadius: "20px",
          border: "1px solid var(--border)",
          background: "var(--card)",
          backgroundImage: "none",
        },
      }}
    >
      <Body {...rest} onClose={onClose} working={working} setWorking={setWorking} />
    </Dialog>
  );
}

function Body({
  formats,
  request,
  scope: counts = null,
  fallbackName,
  onClose,
  working,
  setWorking,
}: Omit<Props, "open"> & {
  working: boolean;
  setWorking: (value: boolean) => void;
}) {
  const t = useTranslations("export");
  const [format, setFormat] = useState<ExportFormat>(formats[0]);
  const [scope, setScope] = useState<ExportScope>("view");
  const [failed, setFailed] = useState("");

  const download = useCallback(async () => {
    if (working) return;
    setWorking(true);
    setFailed("");
    try {
      const { blob, disposition } = await api.exportFile(request(format, scope));
      saveBlob(blob, filenameFrom(disposition, `${fallbackName}.${format}`));
      onClose();
    } catch (error) {
      // No toast system in this app, so the failure stays where the action was
      // — the same choice `MarkdownTable`'s save makes. The server's message is
      // shown verbatim: a 503 here names the missing binary and its install
      // line, which is the only useful thing anyone could be told.
      setFailed(error instanceof Error ? error.message : t("failed"));
    } finally {
      setWorking(false);
    }
  }, [fallbackName, format, onClose, request, scope, setWorking, t, working]);

  return (
    <>
      <DialogTitle sx={{ pb: 1.25 }}>
        <VuiBox display="flex" alignItems="center" gap={1}>
          <Download size={20} />
          <VuiTypography variant="h6" sx={{ color: "var(--foreground)" }}>
            {t("title")}
          </VuiTypography>
        </VuiBox>
      </DialogTitle>

      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <Field label={t("formatLabel")}>
          {formats.map((option) => (
            <ToggleChip
              key={option}
              label={t(`format.${option}`)}
              on={format === option}
              onClick={() => setFormat(option)}
              disabled={working}
            />
          ))}
        </Field>

        {counts && counts.view !== counts.full && (
          <Field label={t("scopeLabel")}>
            <ToggleChip
              label={`${t("scopeView")} · ${t("rows", { count: counts.view })}`}
              on={scope === "view"}
              onClick={() => setScope("view")}
              disabled={working}
            />
            <ToggleChip
              label={`${t("scopeFull")} · ${t("rows", { count: counts.full })}`}
              on={scope === "full"}
              onClick={() => setScope("full")}
              disabled={working}
            />
          </Field>
        )}

        {failed && <Alert severity="error">{failed}</Alert>}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button onClick={onClose} disabled={working}>
          {t("cancel")}
        </Button>
        <Button
          variant="contained"
          onClick={() => void download()}
          disabled={working}
          sx={{
            backgroundColor: "var(--primary)",
            color: "var(--primary-foreground) !important",
            WebkitTextFillColor: "var(--primary-foreground) !important",
            "&:hover": { backgroundColor: "var(--primary-hover)" },
            "&.Mui-disabled": {
              backgroundColor: "var(--muted)",
              color: "var(--control-ink) !important",
              WebkitTextFillColor: "var(--control-ink) !important",
            },
          }}
        >
          {working ? t("working") : t("submit")}
        </Button>
      </DialogActions>
    </>
  );
}

/** A labelled row of chips. Two of them, so the label styling is written once. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <VuiBox display="flex" flexDirection="column" gap={1}>
      <VuiTypography
        variant="caption"
        sx={{
          color: "var(--control-ink)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}
      >
        {label}
      </VuiTypography>
      <VuiBox display="flex" flexWrap="wrap" gap={1}>
        {children}
      </VuiBox>
    </VuiBox>
  );
}
