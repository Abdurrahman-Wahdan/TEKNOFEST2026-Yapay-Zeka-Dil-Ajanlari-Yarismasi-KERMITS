"use client";

import { File as FileGlyph, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import type { ChatAttachments } from "@/lib/chat/types";

import { shortLocation } from "@/lib/chat/page-locator";

import { ContextGlyph } from "./ContextGlyph";

/**
 * Everything staged for the next turn, above the composer's text: images, files,
 * and pieces of the app itself -- a quote, a table row, a whole table.
 *
 * Three chip shapes would be three things to keep in step, so there are two: a
 * thumbnail for anything with a picture, and a labelled chip for everything else.
 * What tells a document from a table is the glyph, which `ContextGlyph` keeps
 * identical here, in the `@` menu and in the transcript.
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
  const contexts = attachments.contexts ?? [];
  const captures = attachments.captures ?? [];
  const hasAny =
    images.length > 0 || files.length > 0 || contexts.length > 0 || captures.length > 0;

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

            {/* A screenshot is a picture, so it gets the thumbnail treatment the
                images get rather than a labelled chip -- what it is, is what it
                looks like. Its `dataUrl` is both the payload and the preview. */}
            {captures.map((capture) => (
              <VuiBox
                key={capture.id}
                className="tf26-chip"
                sx={{
                  position: "relative",
                  width: 56,
                  height: 56,
                  borderRadius: "var(--radius-sm)",
                  overflow: "hidden",
                  backgroundColor: "var(--muted)",
                  border: "1px solid var(--border)",
                }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={capture.dataUrl}
                  alt={capture.label}
                  title={capture.label}
                  style={{ width: "100%", height: "100%", objectFit: "cover" }}
                />
                {attachments.onRemoveCapture && (
                  <VuiBox sx={{ position: "absolute", top: 2, right: 2 }}>
                    <RemoveButton
                      label={t("removeCapture")}
                      onClick={() => attachments.onRemoveCapture?.(capture.id)}
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

            {/* Pieces of the app, drawn as the file chip rather than as a third
                shape: a staged thing is a staged thing, and the glyph is what
                says which kind. Only the subline differs -- a row count where a
                file would show its bytes. */}
            {contexts.map((context) => (
              <VuiBox
                key={context.id}
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
                <VuiBox display="flex" sx={{ color: "var(--control-ink)", flexShrink: 0 }}>
                  <ContextGlyph kind={context.kind} />
                </VuiBox>
                <VuiBox sx={{ minWidth: 0 }}>
                  <VuiTypography
                    variant="caption"
                    color="white"
                    fontWeight="medium"
                    sx={{
                      display: "block",
                      maxWidth: 160,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {context.label}
                  </VuiTypography>
                  <VuiTypography
                    variant="caption"
                    color="text"
                    sx={{
                      display: "block",
                      fontSize: "0.625rem",
                      maxWidth: 160,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {/* The row count when there is one, otherwise the most
                        specific thing known about where it came from -- the
                        column, or failing that the row, the table, the section.
                        Both answer "which thing is this?" for a chip whose label
                        may have been elided. */}
                    {context.count !== undefined
                      ? t("contextRows", { count: context.count })
                      : shortLocation(context.location, context.kind)}
                  </VuiTypography>
                </VuiBox>
                {attachments.onRemoveContext && (
                  <RemoveButton
                    label={t("removeContext")}
                    onClick={() => attachments.onRemoveContext?.(context.id)}
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
