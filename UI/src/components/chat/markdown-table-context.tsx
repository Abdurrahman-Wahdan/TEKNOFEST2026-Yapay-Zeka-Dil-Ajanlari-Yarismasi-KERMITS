"use client";

import { createContext, useContext } from "react";

/**
 * What `MdTable` needs and cannot be passed as a prop.
 *
 * Streamdown owns the element overrides, so `MdTable` receives only the `<table>`'s
 * attributes plus its HAST node. Saving a table needs three more things, all of
 * which `AgentMarkdown` has: whether the message is still streaming, the markdown
 * source (for the heading above the table), and somewhere to send the result.
 *
 * Context rather than threading props, for one specific reason: the `components` map
 * in `AgentMarkdown` is built at module scope. Moving it into the render body to
 * close over props would rebuild it every render, which remounts the entire markdown
 * subtree on every streamed token -- a real, visible regression that looks like a
 * Streamdown bug.
 */
export type MarkdownTableTools = {
  /** True while the message is still arriving. Hides the save control. */
  streaming?: boolean;
  /** The whole message source, for `headingBefore`. */
  source?: string;
};

const MarkdownTableContext = createContext<MarkdownTableTools>({});

export const MarkdownTableProvider = MarkdownTableContext.Provider;

export function useMarkdownTableTools(): MarkdownTableTools {
  return useContext(MarkdownTableContext);
}
