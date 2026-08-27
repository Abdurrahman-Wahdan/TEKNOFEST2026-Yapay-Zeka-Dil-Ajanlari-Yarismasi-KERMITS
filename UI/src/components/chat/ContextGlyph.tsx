"use client";

import { BarChart3, Eye, FileText, Quote, Rows3, Table2 } from "lucide-react";

import type { ContextKind } from "@/lib/chat/types";

/**
 * The picture for a piece of attached UI.
 *
 * One copy, because the same attachment is drawn in three places -- the
 * composer's tray, the `@` menu, and the transcript once it has been sent -- and
 * a chip that changes picture between them reads as a different thing.
 */
export function ContextGlyph({
  kind,
  size = 16,
}: {
  kind: ContextKind | "capture";
  size?: number;
}) {
  switch (kind) {
    case "quote":
      return <Quote size={size} aria-hidden="true" />;
    case "row":
      return <Rows3 size={size} aria-hidden="true" />;
    case "table":
      return <Table2 size={size} aria-hidden="true" />;
    case "chart":
      return <BarChart3 size={size} aria-hidden="true" />;
    case "report":
      // The same sheet of paper the reports list draws, so a chip in the
      // composer is recognisably the thing the user pressed on that page.
      return <FileText size={size} aria-hidden="true" />;
    case "page":
    case "capture":
      // An eye, not a camera. What the user needs to know is that the assistant
      // looked at their page; whether it did that by reading the markup or by
      // photographing it is an implementation detail, and showing a camera
      // invites "why is it taking pictures of my screen?".
      return <Eye size={size} aria-hidden="true" />;
  }
  // Exhaustive: a new `ContextKind` fails to compile here rather than rendering
  // nothing. `page` was added and fell through to no glyph at all.
  return null;
}
