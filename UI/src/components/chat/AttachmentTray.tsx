"use client";

import { File as FileGlyph, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import type { ChatAttachments } from "@/lib/chat/types";

/**
 * Staged images and files, above the composer's text.
 *
 * Built but not currently reachable: there is no upload endpoint yet, so neither
 * surface passes `attachments` and the composer draws no attach button. Shipping
 * the tray now means the day the endpoint lands the work is a prop, not a
 * component -- and it means the design was settled while the design was in front
 * of us.
 */

/** Bytes as something a person reads. */
function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** The circular remove button both chip kinds use. */
function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      display="flex"
      alignItems="center"
      justifyContent="center"
      sx={{
        width: 20,
        height: 20,
        flexShrink: 0,
        border: "none",
        padding: 0,
        cursor: "pointer",
        borderRadius: "var(--radius-full)",
        backgroundColor: "var(--muted)",
        color: "var(--foreground)",
        // Revealed on hover of the chip, but always present for keyboard users --
        // `opacity: 0` would leave it focusable and invisible.
        opacity: 0,
        transition: "opacity 150ms ease",
        ".tf26-chip:hover &, &:focus-visible": { opacity: 1 },
      }}
    >
      <X size={12} />
    </VuiBox>
  );
}

export function AttachmentTray({ attachments }: { attachments: ChatAttachments }) {
  const t = useTranslations("chat");

  const images = attachments.images ?? [];
  const files = attachments.files ?? [];
  const hasAny = images.length > 0 || files.length > 0;

  return (
    <VuiBox
      sx={{
        display: "grid",
        // 0fr -> 1fr animates a height the content decides, which `height: auto`
        // cannot do. The inner overflow:hidden is what makes the collapsed row
        // actually clip.
        gridTemplateRows: hasAny ? "1fr" : "0fr",
        transition: "grid-template-rows 200ms ease-out",
      }}
    >
      <VuiBox sx={{ overflow: "hidden" }}>
        {/*
          One row that scrolls, not a wrapping grid.

          Wrapping made the composer grow a line per few files and pushed the
          controls around; ChatGPT keeps the chips on a single strip above the
          text and scrolls it sideways, so the composer's height is the same
          whether one file is attached or nine. `flexShrink: 0` on the children
          is what stops them being squeezed thinner instead of overflowing.
        */}
        {hasAny && (
          <VuiBox
            display="flex"
            alignItems="center"
            gap={1}
            px={1.75}
            pt={1.75}
            sx={{
              flexWrap: "nowrap",
              overflowX: "auto",
              // The strip is its own scroll area; a horizontal flick here must not
              // turn into the page scrolling behind it.
              overscrollBehaviorX: "contain",
              // No visible scrollbar eating into the chips' height.
              scrollbarWidth: "none",
              "&::-webkit-scrollbar": { display: "none" },
              "& > *": { flexShrink: 0 },
            }}
          >
            {images.map((image) => (
              <VuiBox
                key={image.id}
                className="tf26-chip"
                sx={{
                  position: "relative",
                  width: 56,
                  height: 56,
                  borderRadius: "var(--radius-sm)",
                  overflow: "hidden",
                  backgroundColor: "var(--muted)",
                }}
              >
                {/* A staged local preview via object URL, not a remote asset, so
                    next/image would add a loader for no benefit. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={image.url}
                  alt={image.filename}
                  style={{ width: "100%", height: "100%", objectFit: "cover" }}
                />
                {attachments.onRemoveImage && (
                  <VuiBox sx={{ position: "absolute", top: 2, right: 2 }}>
                    <RemoveButton
                      label={t("removeImage")}
                      onClick={() => attachments.onRemoveImage?.(image.id)}
                    />
                  </VuiBox>
                )}
              </VuiBox>
            ))}

            {files.map((file) => (
              <VuiBox
                key={file.id}
                className="tf26-chip"
                display="flex"
                alignItems="center"
                gap={1}
                px={1}
                py={0.75}
                sx={{
                  borderRadius: "var(--radius-sm)",
                  backgroundColor: "var(--muted)",
                  border: "1px solid var(--border)",
                }}
              >
                {/* --control-ink rather than --text-faint: the glyph is what
                    marks this chip as a document instead of a picture, and
                    --text-faint is 2.49:1 on the dark surface -- effectively
                    invisible. Faint is for decoration; this carries meaning. */}
                <VuiBox display="flex" sx={{ color: "var(--control-ink)", flexShrink: 0 }}>
                  <FileGlyph size={16} />
                </VuiBox>
                <VuiBox sx={{ minWidth: 0 }}>
                  <VuiTypography
                    variant="caption"
                    color="white"
                    fontWeight="medium"
                    sx={{
                      display: "block",
                      maxWidth: 140,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {file.filename}
                  </VuiTypography>
                  {file.size !== undefined && (
                    <VuiTypography
                      variant="caption"
                      color="text"
                      sx={{ display: "block", fontSize: "0.625rem" }}
                    >
                      {formatSize(file.size)}
                    </VuiTypography>
                  )}
                </VuiBox>
                {attachments.onRemoveFile && (
                  <RemoveButton
                    label={t("removeFile")}
                    onClick={() => attachments.onRemoveFile?.(file.id)}
                  />
                )}
              </VuiBox>
            ))}
          </VuiBox>
        )}
      </VuiBox>
    </VuiBox>
  );
}
