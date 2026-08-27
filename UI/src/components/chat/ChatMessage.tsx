"use client";

import dynamic from "next/dynamic";
import { ArrowRight, ExternalLink, File as FileGlyph, Image as ImageGlyph } from "lucide-react";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import type { AgentMessage, ChatStage } from "@/lib/chat/types";
import { navLabel } from "@/lib/nav-label";
import { safeWebSource, siteSection, sourceGroup } from "@/lib/chat/source-group";

import { ContextGlyph } from "./ContextGlyph";
import { MessageActions } from "./MessageActions";

/**
 * Streamdown pulls in Shiki, and Shiki is large. Loading it lazily keeps it off
 * the critical path for every dashboard page -- which matters because the popup
 * mounts on all of them, and most visits never open it.
 *
 * `ssr: false` because there is nothing to server-render: assistant text only
 * exists after a client-side stream. The cost is a brief fallback the first time
 * an answer appears, which is why the fallback below is the plain text rather
 * than a spinner -- the words are readable while the renderer arrives.
 */
const AgentMarkdown = dynamic(
  () => import("./AgentMarkdown").then((m) => m.AgentMarkdown),
  {
    ssr: false,
    loading: () => null,
  },
);

export function ChatMessage({
  message,
  streaming,
  isLast,
  stage,
  compact = false,
}: {
  message: AgentMessage;
  /** True when this is the last message and the stream is still open. */
  streaming?: boolean;
  /**
   * True for the newest message in the transcript.
   *
   * Only the action row reads it, and only to decide whether to offer a retry:
   * a retry replaces the turn it sits under, and the one turn that can be
   * replaced without orphaning the answers built on top of it is the last.
   */
  isLast?: boolean;
  /**
   * What the agent is doing, when the server has said. Only ever reaches the
   * pre-answer placeholder below -- once there is text to show, the text is the
   * status.
   */
  stage?: ChatStage;
  /** Uses the denser type and source-card scale of the floating assistant. */
  compact?: boolean;
}) {
  const t = useTranslations("chat");
  // The drawer's own words for the sections our pages live in, so a source card
  // and the page it opens are not labelled two different ways.
  const tNav = useTranslations("nav");

  /**
   * Whether this message has finished and has something to act on.
   *
   * Not while it streams: a copy button next to a half-written answer copies half
   * an answer, and read-aloud would start reading a sentence that is still being
   * written. An errored turn qualifies on purpose -- it has no text, so the row
   * comes down to the retry, which is the one thing a failed answer needs.
   */
  const settled =
    message.role === "assistant" &&
    !streaming &&
    message.parts.some(
      (part) => (part.type === "text" && part.text.trim()) || part.type === "error",
    );
  const answerText = message.parts
    .flatMap((part) => (part.type === "text" && part.text.trim() ? [part.text] : []))
    .join("\n\n");

  return (
    <VuiBox display="flex" flexDirection="column" gap={compact ? 0.75 : 1}>
      {message.parts.map((part, index) => {
        if (part.type === "error") {
          return (
            <VuiBox
              key={index}
              px={2}
              py={1.5}
              sx={{
                // Outlined, not filled -- the same rule the app's status pills
                // follow, so an error here reads as the app's error and not as a
                // pasted-in alert.
                border: "1px solid var(--danger)",
                backgroundColor: "var(--danger-subtle)",
                borderRadius: "var(--radius-sm)",
                alignSelf: "flex-start",
                maxWidth: "100%",
              }}
            >
              <VuiTypography variant="button" color="white" fontWeight="medium">
                {part.title ?? t("errorTitle")}
              </VuiTypography>
              <VuiTypography
                variant="button"
                fontWeight="regular"
                color="text"
                sx={{ display: "block", mt: 0.25 }}
              >
                {part.message}
              </VuiTypography>
            </VuiBox>
          );
        }

        if (part.type === "citations") {
          const sources = part.sources.flatMap((source) => {
            const group = sourceGroup(source.sourceType, source.url);
            return group ? [{ source, group }] : [];
          });
          if (sources.length === 0) return null;
          /*
            Our own pages come last, deliberately. Everything above them supports
            a claim in the answer; a comparison table does not -- the assistant
            offers it as somewhere to go next. Putting it first would read as the
            answer's primary source, which is the one thing it must never be.
          */
          const groups = (
            [
              { key: "online", label: t("onlineSources") },
              { key: "knowledge-base", label: t("knowledgeBaseSources") },
              { key: "site", label: t("sitePageSources") },
            ] as const
          )
            .map((group) => ({
              ...group,
              sources: sources.filter(({ group: g }) => g === group.key),
            }))
            .filter((group) => group.sources.length > 0);

          return (
            <VuiBox
              key={index}
              component="section"
              aria-label={t("sources")}
              mt={compact ? 0.25 : 0.75}
              sx={{ maxWidth: "100%", minWidth: 0 }}
            >
              <VuiTypography
                variant="caption"
                fontWeight="medium"
                color="text"
                sx={{
                  display: "block",
                  mb: compact ? 0.4 : 0.75,
                  opacity: 0.78,
                  ...(compact ? { fontSize: "0.6875rem", lineHeight: 1.25 } : {}),
                }}
              >
                {t("sources")}
              </VuiTypography>
              {groups.map((group) => (
                <VuiBox key={group.key} mb={compact ? 0.625 : 1}>
                  <VuiTypography
                    variant="caption"
                    fontWeight="medium"
                    color="text"
                    sx={{
                      display: "block",
                      mb: compact ? 0.3 : 0.5,
                      opacity: 0.68,
                      ...(compact
                        ? { fontSize: "0.6875rem", lineHeight: 1.2 }
                        : {}),
                    }}
                  >
                    {group.label}
                  </VuiTypography>
                  <VuiBox
                    display={compact ? "grid" : "flex"}
                    flexWrap={compact ? undefined : "wrap"}
                    gap={compact ? 0.4 : 0.75}
                    sx={
                      compact
                        ? { gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }
                        : undefined
                    }
                  >
                    {group.sources.map(({ source }, sourceIndex) => (
                      <VuiBox
                        key={source.url}
                        component="a"
                        href={source.url}
                        /* Our own pages stay in the app; everything else is a
                           bank's site and opens in its own tab. Same rule
                           `AgentMarkdown` applies to the links in the prose. */
                        {...(group.key === "site"
                          ? {}
                          : { target: "_blank", rel: "noopener noreferrer" })}
                        title={source.url}
                        display="flex"
                        alignItems="center"
                        gap={compact ? 0.5 : 0.75}
                        px={compact ? 0.75 : 1.25}
                        py={compact ? 0.4 : 0.75}
                        sx={{
                          minWidth: 0,
                          maxWidth: "100%",
                          ...(compact ? { width: "100%" } : {}),
                          color: "var(--control-ink)",
                          border: "1px solid var(--border)",
                          borderRadius: compact
                            ? "var(--radius-sm)"
                            : "var(--radius-md)",
                          textDecoration: "none",
                          backgroundColor: "transparent",
                          transition: "border-color 120ms ease, background-color 120ms ease",
                          "&:hover": {
                            borderColor: "var(--border-strong)",
                            backgroundColor: "var(--muted)",
                          },
                          "&:focus-visible": {
                            outline: "2px solid var(--info)",
                            outlineOffset: "2px",
                          },
                        }}
                      >
                        <VuiTypography
                          variant="caption"
                          fontWeight="bold"
                          color="inherit"
                          sx={{
                            color: "var(--primary-strong)",
                            flex: "0 0 auto",
                            ...(compact
                              ? { fontSize: "0.75rem", lineHeight: 1.2 }
                              : {}),
                          }}
                        >
                          {sourceIndex + 1}
                        </VuiTypography>
                        <VuiBox minWidth={0}>
                          <VuiTypography
                            variant="caption"
                            fontWeight="medium"
                            color="text"
                            sx={{
                              display: "block",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                              maxWidth: "24rem",
                              ...(compact
                                ? { fontSize: "0.75rem", lineHeight: 1.2 }
                                : {}),
                            }}
                          >
                            {source.title || source.bank || source.url}
                          </VuiTypography>
                          <VuiTypography
                            variant="caption"
                            fontWeight="regular"
                            color="text"
                            sx={{
                              display: "block",
                              opacity: 0.65,
                              ...(compact
                                ? {
                                    fontSize: "0.6875rem",
                                    lineHeight: 1.2,
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                  }
                                : {}),
                            }}
                          >
                            {group.key === "site"
                              ? navLabel(tNav, siteSection(source.url), siteSection(source.url))
                              : (safeWebSource(source.url)?.hostname ?? source.url)}
                          </VuiTypography>
                        </VuiBox>
                        {/* An arrow for a page in this app, the external-link
                            glyph only for links that really do leave it. */}
                        {group.key === "site" ? (
                          <ArrowRight
                            size={compact ? 12 : 13}
                            aria-hidden="true"
                          />
                        ) : (
                          <ExternalLink
                            size={compact ? 12 : 13}
                            aria-hidden="true"
                          />
                        )}
                      </VuiBox>
                    ))}
                  </VuiBox>
                </VuiBox>
              ))}
            </VuiBox>
          );
        }

        /**
         * A piece of the app that travelled with this turn.
         *
         * A reference line, never the body. The body is a serialised table --
         * fifty rows of markdown -- and the user has already seen it on the page
         * they attached it from; repeating it in the transcript would bury their
         * own question. What this has to answer is "what did I send?", which is
         * the label and where it came from.
         */
        if (part.type === "context") {
          return (
            <VuiBox
              key={index}
              alignSelf={message.role === "user" ? "flex-end" : "flex-start"}
              display="flex"
              alignItems="center"
              gap={0.75}
              px={1.25}
              py={0.75}
              sx={{
                maxWidth: "75%",
                minWidth: 0,
                // Outlined rather than filled, so it reads as a citation next to
                // the user's filled bubble instead of as a second thing they said.
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-md)",
                color: "var(--control-ink)",
              }}
            >
              <ContextGlyph kind={part.kind} />
              <VuiTypography
                variant="caption"
                fontWeight="medium"
                color="text"
                sx={{
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {/*
                  The assistant looking at the page gets one fixed, translated
                  phrase; everything the user attached themselves keeps its own
                  label.

                  The label is deliberately not shown for a tool: it is written for
                  the agent and read "read the page (1 table(s), 1 control(s))",
                  which tells the user nothing they wanted and quite a lot about
                  our internals. It still travels on the request -- it is just not
                  page furniture.
                */}
                {part.kind === "capture" ? t("readPage") : part.label}
              </VuiTypography>
            </VuiBox>
          );
        }

        if (part.type === "attachment") {
          return (
            <VuiBox
              key={index}
              alignSelf="flex-end"
              display="flex"
              alignItems="center"
              gap={0.75}
              px={1.25}
              py={0.75}
              sx={{
                maxWidth: "75%",
                minWidth: 0,
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-md)",
                color: "var(--control-ink)",
              }}
            >
              {part.kind === "image" ? <ImageGlyph size={16} /> : <FileGlyph size={16} />}
              <VuiTypography
                variant="caption"
                fontWeight="medium"
                color="text"
                sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              >
                {part.filename}
              </VuiTypography>
            </VuiBox>
          );
        }

        if (message.role === "user") {
          return (
            <VuiBox
              key={index}
              alignSelf="flex-end"
              px={2.25}
              py={1.25}
              sx={{
                // ChatGPT's proportions: a rounded rectangle that stops well
                // short of the full column so the turn-taking is legible at a
                // glance, and a fill one step off the page rather than a tinted
                // panel. The earlier version used `--accent` with a border, which
                // in the light theme is a pale blue box and read as a callout
                // rather than as something the user said.
                maxWidth: "75%",
                // Flex, so the text is centred in the bubble rather than sitting
                // on a baseline. The padding was already symmetric at 10px, but
                // the block's own line-box strut is taller than the text's inline
                // box, so the glyphs landed 19.5px below the top edge and 15px
                // above the bottom -- 4.5px out, which is exactly enough to read
                // as "not quite in the middle".
                display: "flex",
                alignItems: "center",
                // A flex item will not shrink below its content width without
                // this, so a long message would stop wrapping.
                "& > *": { minWidth: 0 },
                backgroundColor: "var(--muted)",
                border: "none",
                // Large, but not a pill -- a multi-line question in a pill looks
                // like a button.
                borderRadius: "20px",
                // The user's own words are never markdown: rendering them as
                // markdown would let a stray asterisk silently reformat what they
                // typed. Their newlines are still honoured.
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              <VuiTypography
                variant="button"
                fontWeight="regular"
                color="white"
                sx={{
                  lineHeight: compact ? 1.45 : 1.6,
                  ...(compact ? { fontSize: "0.8125rem" } : {}),
                }}
              >
                {part.text}
              </VuiTypography>
            </VuiBox>
          );
        }

        // The assistant, mid-stream, before its first token: nothing to render
        // yet, and an empty bubble would flash on every send.
        //
        // The wait here is long -- 83s on a ten-bank comparison -- and it used to
        // read "Düşünüyor…" for all of it, which is indistinguishable from being
        // stuck. `stage` names the part of the turn the server has reached; the
        // generic label stays as the fallback for the gap before the first stage
        // frame and for any stage this build has no word for.
        if (!part.text) {
          return streaming ? (
            <VuiTypography
              key={index}
              variant="button"
              fontWeight="regular"
              color="text"
              sx={{ opacity: 0.6 }}
            >
              {stage ? t(`stage.${stage}`) : t("thinking")}
            </VuiTypography>
          ) : null;
        }

        return (
          <VuiBox
            key={index}
            alignSelf="flex-start"
            // No bubble on the assistant's side. Its answers carry tables and code
            // blocks, and a bubble around a table is a box inside a box.
            sx={{ maxWidth: "100%", minWidth: 0, width: "100%" }}
          >
            <AgentMarkdown compact={compact} streaming={streaming}>
              {part.text}
            </AgentMarkdown>
          </VuiBox>
        );
      })}

      {/* Under the whole message, not inside the parts loop: an answer with a
          citations block is still one answer, and a row of buttons between the
          prose and its sources would read as belonging to the prose alone. */}
      {settled && (
        <MessageActions
          messageId={message.id}
          markdown={answerText}
          isLast={Boolean(isLast)}
          feedback={message.feedback}
        />
      )}
    </VuiBox>
  );
}
