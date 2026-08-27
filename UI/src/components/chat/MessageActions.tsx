"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  RefreshCw,
  Square,
  ThumbsDown,
  ThumbsUp,
  Volume2,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { RoundButton } from "@/components/ui/RoundButton";
import { VuiBox } from "@/components/vision";
import { useChat } from "@/lib/chat/ChatProvider";
import { useSpeech } from "@/lib/chat/speech";
import type { MessageFeedback } from "@/lib/chat/types";

import { FeedbackDialog } from "./FeedbackDialog";

/** How long the copy button stays a tick before going back to being a copy button. */
const COPIED_MS = 2000;

/**
 * The row's button box and the glyph inside it.
 *
 * Smaller than the composer's 36/20: five buttons under every answer at the
 * composer's size start to outweigh the answer, and 32 is what ChatGPT's own row
 * measures.
 *
 * `INK_INSET_PX` is the same idea as `ChatComposer`'s -- a glyph centred in a
 * larger box starts its ink some way in, so a row of buttons flush to its
 * container sits visibly indented from the text above it. Pulling the row back
 * by exactly that inset is what puts the first glyph on the answer's left edge.
 */
const BUTTON_PX = 32;
const GLYPH_PX = 17;
const INK_INSET_PX = (BUTTON_PX - GLYPH_PX) / 2;

/** The same clipboard behavior for both sides of the conversation. */
export function MessageCopyButton({ text, label }: { text: string; label: string }) {
  const t = useTranslations("chat");
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copiedTimer.current) clearTimeout(copiedTimer.current);
  }, []);

  const copy = useCallback(() => {
    if (!navigator.clipboard?.writeText) return;
    void navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        if (copiedTimer.current) clearTimeout(copiedTimer.current);
        copiedTimer.current = setTimeout(() => setCopied(false), COPIED_MS);
      })
      .catch(() => {
        // Permission denied or an insecure origin: do not claim it was copied.
      });
  }, [text]);

  return (
    <RoundButton
      size={BUTTON_PX}
      label={copied ? t("copied") : label}
      onClick={copy}
    >
      {copied ? <Check size={GLYPH_PX} /> : <Copy size={GLYPH_PX} />}
    </RoundButton>
  );
}

/**
 * What you can do with an answer once it has arrived.
 *
 * Modelled on ChatGPT's row and deliberately shorter than it: copy, try again
 * and read aloud on the left, the two feedback thumbs on the right, and no "…"
 * menu. There is no second-tier action in this app to put behind one, and a
 * menu that opens onto nothing is a button that lies.
 *
 * **Always visible, never revealed on hover.** ChatGPT fades its row in under
 * the pointer and then spends a good deal of CSS putting it back for touch and
 * for `cant-hover` -- because a hover reveal on a phone is a control nobody can
 * reach. This app has a real phone layout, so the row simply stays.
 */
export function MessageActions({
  messageId,
  /** The answer's markdown, exactly as the model wrote it. */
  markdown,
  /** Whether this is the newest answer, and so the one a retry would replace. */
  isLast,
  feedback,
}: {
  messageId: string;
  markdown: string;
  isLast: boolean;
  feedback?: MessageFeedback;
}) {
  const t = useTranslations("chat");
  const locale = useLocale();
  const { retry, canRetry } = useChat();
  // BCP-47 from the routing locale, so a second language gets its own voice
  // without this file learning about it. "tr" alone picks a Turkish voice on
  // every platform tested; the region makes the match exact where one exists.
  const speech = useSpeech(messageId, locale === "tr" ? "tr-TR" : locale);

  const [dialogRating, setDialogRating] = useState<"up" | "down" | null>(null);
  const vote = feedback?.rating ?? null;

  const hasText = markdown.trim().length > 0;

  return (
    <VuiBox
      component="div"
      role="group"
      aria-label={t("responseActions")}
      display="flex"
      alignItems="center"
      // Flush with the answer's left edge. See INK_INSET_PX.
      ml={`-${INK_INSET_PX}px`}
      mt={0.5}
      sx={{ width: "100%" }}
    >
      {hasText && (
        <MessageCopyButton text={markdown} label={t("copyResponse")} />
      )}

      {/*
        Only on the newest answer. A retry replaces the turn it belongs to, and
        the server rewinds exactly one exchange -- offering it halfway up a
        conversation would promise to rewrite an answer with four turns built on
        top of it. `canRetry` is separately false while a turn is streaming and
        for a turn that cannot be reproduced faithfully.
      */}
      {isLast && canRetry && (
        <RoundButton size={BUTTON_PX} label={t("retry")} onClick={retry}>
          <RefreshCw size={GLYPH_PX} />
        </RoundButton>
      )}

      {/* Hidden rather than disabled where the browser has no synthesiser: this
          is an enhancement, and a permanently dead button is worse than no
          button. */}
      {hasText && speech.supported && (
        <RoundButton
          size={BUTTON_PX}
          label={speech.speaking ? t("readAloudStop") : t("readAloud")}
          active={speech.speaking}
          onClick={() => speech.toggle(markdown)}
        >
          {speech.speaking ? <Square size={GLYPH_PX - 2} fill="currentColor" /> : <Volume2 size={GLYPH_PX} />}
        </RoundButton>
      )}

      <VuiBox sx={{ flex: 1 }} />

      {/* Far right, apart from the rest: everything on the left acts on the
          answer, and these two are about it. */}
      <RoundButton
        size={BUTTON_PX}
        label={t("goodResponse")}
        active={vote === "up"}
        onClick={() => setDialogRating("up")}
      >
        <ThumbsUp size={GLYPH_PX} fill={vote === "up" ? "currentColor" : "none"} />
      </RoundButton>
      <RoundButton
        size={BUTTON_PX}
        label={t("badResponse")}
        active={vote === "down"}
        onClick={() => setDialogRating("down")}
      >
        <ThumbsDown size={GLYPH_PX} fill={vote === "down" ? "currentColor" : "none"} />
      </RoundButton>
      {dialogRating && (
        <FeedbackDialog
          open
          messageId={messageId}
          rating={dialogRating}
          existing={feedback}
          onClose={() => setDialogRating(null)}
        />
      )}
    </VuiBox>
  );
}
