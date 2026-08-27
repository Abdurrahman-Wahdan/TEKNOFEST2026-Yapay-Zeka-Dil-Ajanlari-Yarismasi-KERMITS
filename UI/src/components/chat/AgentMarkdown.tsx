"use client";

import { createCodePlugin } from "@streamdown/code";
import { styled } from "@mui/material/styles";
import { Check, Copy } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo } from "react";
import { Streamdown } from "streamdown";

import { VuiBox, VuiTypography } from "@/components/vision";
import { normaliseAgentMarkdown } from "@/lib/chat/markdown-normalize";
import { internalTableHref } from "@/lib/table-url";

import { MdTable, MdTbody, MdTd, MdTh, MdThead, MdTr } from "./MarkdownTable";
import { MarkdownTableProvider } from "./markdown-table-context";
import { domProps, type El } from "./markdown-dom";

/**
 * The assistant's markdown.
 *
 * Streamdown rather than react-markdown because the agent's answer arrives a few
 * characters at a time, and a normal markdown renderer handed half a table or an
 * unclosed code fence either drops it or renders it broken -- so a streaming
 * answer flickers through a series of wrong layouts before settling. Streamdown
 * closes the open syntax itself as it goes, which is the entire reason it is
 * here.
 *
 * It also sanitises (`rehype-harden`). The agent's output is untrusted text; this
 * is the difference between rendering it and running it.
 *
 * Everything below is either a bridge to this app's design -- the element
 * overrides, the lucide icons, the translated control labels -- or a deliberate
 * narrowing of what Streamdown will do.
 */

/**
 * Shiki, as an opt-in plugin package.
 *
 * Streamdown v2 moved highlighting, mermaid and math out of the core into
 * separate packages, so installing only `@streamdown/code` is what keeps mermaid
 * and KaTeX out of the bundle entirely.
 *
 * Created once at module scope, not per render: the plugin owns Shiki's
 * highlighter and its lazily-loaded grammars, and rebuilding it on every token
 * would re-fetch a grammar mid-stream.
 *
 * Two themes, light and dark. Streamdown emits both and switches with CSS, so
 * this follows the app's theme toggle without a re-render.
 */
const code = createCodePlugin({ themes: ["github-light", "github-dark"] });

/**
 * Element overrides, so the answer lands on the app's type scale instead of
 * Streamdown's prose defaults.
 *
 * Only the elements the agent actually emits are overridden. Anything left out
 * keeps Streamdown's styling, which reads the same palette tokens this app
 * defines -- so the fallback is already close, and the list stays short.
 */
const components = {
  table: MdTable,
  thead: MdThead,
  tbody: MdTbody,
  tr: MdTr,
  th: MdTh,
  td: MdTd,

  p: (props: El<"p">) => (
    <VuiTypography
      variant="button"
      fontWeight="regular"
      color="inherit"
      sx={{ color: "var(--foreground)", display: "block", lineHeight: 1.7, my: 1 }}
      {...domProps(props)}
    />
  ),

  // One step down from the page's own h1/h2: these are headings *inside* a
  // message, and matching the page title would make an answer look like a new
  // page.
  h1: (props: El<"h1">) => (
    <VuiTypography
      variant="lg"
      color="inherit"
      fontWeight="bold"
      // Do not use Vision's legacy `white` role here. Markdown is rendered
      // inside the Tailwind-themed chat surface, and that MUI role can retain
      // dark-theme ink for one render while the light theme is active. The
      // shared CSS token switches atomically with the page theme.
      sx={{ color: "var(--foreground)", mt: 2.5, mb: 1 }}
      {...domProps(props)}
    />
  ),
  h2: (props: El<"h2">) => (
    <VuiTypography
      variant="button"
      color="inherit"
      fontWeight="bold"
      sx={{
        color: "var(--foreground)",
        display: "block",
        mt: 2.5,
        mb: 1,
        fontSize: "1rem",
      }}
      {...domProps(props)}
    />
  ),
  h3: (props: El<"h3">) => (
    <VuiTypography
      variant="button"
      color="inherit"
      fontWeight="bold"
      sx={{
        color: "var(--foreground)",
        display: "block",
        mt: 2,
        mb: 0.5,
      }}
      {...domProps(props)}
    />
  ),

  /**
   * Only external links open a new tab. An in-app link -- the agent pointing at
   * a comparison table with `/tr/kampanyalar?tablo=...` -- stays in the app, so
   * following it does not abandon the conversation.
   *
   * `target` and `rel` are DELETED rather than just left unset. Streamdown's own
   * anchor renderer hardcodes `target="_blank"` and hands it down to this
   * override in `props`, and `domProps` spreads last -- so an earlier
   * conditional was silently overridden and every link, relative ones included,
   * opened a new tab. Measured on a real answer: a `/tr/kampanyalar?tablo=`
   * link rendered with `target="_blank"`.
   *
   * Copy-and-delete rather than a destructure, matching `domProps` itself: it
   * leaves no discarded binding for the linter.
   */
  a: (props: El<"a">) => {
    const rest = domProps(props) as Record<string, unknown>;
    delete rest.target;
    delete rest.rel;
    /*
      A link to one of our own comparison tables is forced back to its in-app
      address, whatever host the answer gave it. The assistant is handed a
      relative address and sometimes prefixes a host it invented, which would
      otherwise render as an external link to a dead domain. See
      `internalTableHref`.
    */
    const internal = internalTableHref(props.href);
    if (internal) rest.href = internal;
    return (
      <VuiTypography
        component="a"
        variant="button"
        fontWeight="regular"
        color="inherit"
        sx={{ color: "var(--primary-strong)", textDecoration: "underline" }}
        {...rest}
        {...(!internal && props.href?.startsWith("http")
          ? { target: "_blank", rel: "noopener noreferrer" }
          : {})}
      />
    );
  },

  strong: (props: El<"strong">) => (
    <VuiTypography
      component="strong"
      variant="button"
      fontWeight="bold"
      color="inherit"
      sx={{ color: "var(--foreground)" }}
      {...domProps(props)}
    />
  ),

  ul: (props: El<"ul">) => (
    <VuiBox
      component="ul"
      sx={{ pl: 2.5, my: 1, listStyle: "disc" }}
      {...domProps(props)}
    />
  ),
  ol: (props: El<"ol">) => (
    <VuiBox
      component="ol"
      sx={{ pl: 2.5, my: 1, listStyle: "decimal" }}
      {...domProps(props)}
    />
  ),
  li: (props: El<"li">) => (
    <VuiBox
      component="li"
      sx={{
        display: "list-item",
        color: "var(--foreground)",
        fontSize: "0.875rem",
        lineHeight: 1.7,
        my: 0.25,
        // A paragraph inside a list item is block-level and would push the text
        // onto its own line below the marker.
        "& p": { display: "inline", my: 0 },
      }}
      {...domProps(props)}
    />
  ),

  blockquote: (props: El<"blockquote">) => (
    <VuiBox
      component="blockquote"
      sx={{
        my: 1.5,
        pl: 2,
        // The palette's flat theme separates surfaces with borders, not fills, so
        // a quote is a rule rather than a tinted block.
        borderLeft: "2px solid var(--border-strong)",
        color: "var(--text-faint)",
        "& p": { color: "inherit" },
      }}
      {...domProps(props)}
    />
  ),
};

/**
 * Streamdown's own chrome, brought into the app's design language.
 *
 * Its code block ships as a box inside a box -- a `bg-sidebar` card wrapping a
 * separately-bordered `bg-background` body -- which in a flat, border-separated
 * theme reads as two nested panels rather than one code block. On top of that the
 * language label sits in its own 32px header bar and the copy button lives in a
 * bordered, backdrop-blurred pill floating above the body, so a single fenced
 * snippet arrives as three stacked pieces of furniture. It also sets
 * `content-visibility: auto` with `contain-intrinsic-size: 200px`, so a
 * four-line snippet reserves 200px and renders with a large empty gap above it,
 * and it prints Shiki's line numbers in a gutter too narrow to separate them
 * from the code (`1SELECT`).
 *
 * The target is what ChatGPT draws: ONE grey rounded panel, the language label
 * inside it at top-left, a bare copy icon inside it at top-right, code below.
 * Streamdown's DOM order (header, actions, body) already matches that reading
 * order, so no element moves -- the wrapper takes over the panel's look, the body
 * gives it up, and the actions are lifted out of flow onto the header's line.
 *
 * These are styled through the `data-streamdown` attributes, which are the
 * documented hooks for exactly this, and scoped to this wrapper so nothing leaks
 * into the rest of the app. Every colour is a palette custom property, so the
 * panel follows the light/dark toggle without a second rule.
 */
const MarkdownScope = styled("div")({
  minWidth: 0,

  // The outer wrapper becomes THE panel -- it is the only ancestor that contains
  // the header, the copy button and the code, so it is the only element that can
  // draw one border around all three. Streamdown had it as a `bg-sidebar` card
  // with a `rounded-xl` radius and a `gap-2` column, which is the outer half of
  // the box-in-a-box; `position: relative` is what lets the copy button be
  // pinned to its top-right corner below.
  '& [data-streamdown="code-block"]': {
    position: "relative",
    margin: "1rem 0",
    padding: "0.5rem 0.875rem 0.75rem",
    gap: 0,
    background: "var(--muted)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    overflow: "hidden",
    // What was reserving the empty 200px.
    contentVisibility: "visible",
    containIntrinsicSize: "auto",
  },

  // The language label, now inside the panel's top padding rather than in a bar
  // of its own. The fixed `h-8` is what made it read as a header strip, so it is
  // dropped for the label's own line height. The right padding keeps a long
  // language name off the copy button, which is out of flow and cannot push it.
  // (Streamdown's inner span already supplies mono + lowercase.)
  //
  // --control-ink, not --text-faint: these are words, and --text-faint is 2.49:1
  // on the dark card -- for decoration only. See the token's own note in
  // tailwind.css.
  '& [data-streamdown="code-block-header"]': {
    height: "auto",
    minHeight: 0,
    padding: "0 1.75rem 0.375rem 0",
    fontSize: "0.75rem",
    lineHeight: 1.4,
    fontFamily: "var(--font-mono)",
    textTransform: "lowercase",
    color: "var(--control-ink)",
  },

  // The wrapper Streamdown puts around the buttons -- no data attribute of its
  // own, hence the `:has`. It is `sticky top-2 -mt-10 h-8`, a negative margin
  // sized to overlap the 32px header bar that no longer exists: left alone it
  // pulls the button up out of the panel and then slides it down the block as
  // you scroll. Absolute against the panel instead, so it cannot drift.
  '& [data-streamdown="code-block"] > div:has(> [data-streamdown="code-block-actions"])':
    {
      position: "absolute",
      top: "0.5rem",
      right: "0.625rem",
      margin: 0,
      padding: 0,
      height: "auto",
      zIndex: 1,
    },

  // The pill around the button: border, translucent `bg-sidebar` and a
  // backdrop-blur, all of which only make sense for a control floating over the
  // code. Inside the panel it is a second surface on top of a surface, so every
  // part of it is removed and the button is left bare.
  '& [data-streamdown="code-block-actions"]': {
    background: "transparent",
    border: "none",
    borderRadius: 0,
    padding: 0,
    backdropFilter: "none",
    boxShadow: "none",
  },

  // The copy control as a bare icon: quiet until hovered, and with a real focus
  // ring, since removing the pill also removed the only thing that showed
  // keyboard focus. Streamdown's own `text-muted-foreground` is 3.88:1 on the
  // dark card and made the icon read as disabled, so it rests on --control-ink
  // and brightens to --foreground.
  '& [data-streamdown="code-block-copy-button"]': {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "0.125rem",
    background: "transparent",
    border: "none",
    borderRadius: "var(--radius-sm)",
    color: "var(--control-ink)",
    transition: "color 120ms ease",
    "&:hover": {
      background: "transparent",
      color: "var(--foreground)",
    },
    "&:focus-visible": {
      outline: "2px solid var(--ring)",
      outlineOffset: "2px",
    },
  },

  // The body is now just the code. It kept `bg-background`, `border-border`,
  // `rounded-md` and `p-4` -- the inner half of the box-in-a-box, and a second
  // rounded rectangle inside the panel if left on. `overflow-x-auto` is
  // deliberately not touched: a long line still scrolls inside the panel.
  '& [data-streamdown="code-block-body"]': {
    background: "transparent",
    border: "none",
    borderRadius: 0,
    padding: 0,
  },

  // Shiki's line numbers, which Streamdown draws with a CSS counter in a
  // ::before. Hidden by removing the pseudo-element rather than by blanking its
  // `content` -- emotion requires content values to carry their own quotes, and
  // an unquoted one throws at render.
  '& [data-streamdown="code-block-body"] code > span::before': {
    display: "none",
  },

  // Inline code, which Streamdown leaves to the host.
  "& :not(pre) > code": {
    fontFamily: "var(--font-mono)",
    fontSize: "0.8125em",
    padding: "0.1em 0.35em",
    borderRadius: "var(--radius-sm)",
    background: "var(--muted)",
    border: "1px solid var(--border)",
  },
});

export function AgentMarkdown({
  children,
  streaming,
}: {
  children: string;
  /** True while this message is still arriving. Drives Streamdown's caret. */
  streaming?: boolean;
}) {
  const t = useTranslations("chat");

  // Streamdown ships its own English UI strings for the copy button. Passed
  // through next-intl so a Turkish user does not get one stray English tooltip.
  const translations = useMemo(
    () => ({ copyCode: t("copyCode"), copied: t("copied") }),
    [t],
  );

  const source = useMemo(() => normaliseAgentMarkdown(children), [children]);

  // The table override needs the streaming flag and the message source, and it
  // cannot be handed them as props: `components` is built at module scope, and
  // rebuilding it per render remounts the whole markdown tree on every token.
  const tableTools = useMemo(() => ({ streaming, source }), [streaming, source]);

  return (
    <MarkdownTableProvider value={tableTools}>
    <MarkdownScope>
      <Streamdown
        mode={streaming ? "streaming" : "static"}
        // The reason this component exists. Explicit rather than relying on the
        // default, because turning it off silently reintroduces the flicker.
        parseIncompleteMarkdown
        plugins={{ code }}
        shikiTheme={["github-light", "github-dark"]}
        // Copy on code blocks only. Table copy/download and fullscreen are
        // deliberately off: each adds more untranslated UI than it earns, and they
        // can be switched on with their message keys when someone asks for them.
        controls={{
          code: { copy: true, download: false },
          table: false,
          mermaid: false,
        }}
        // Streamdown's own icons are lucide-shaped but not lucide; passing the real
        // ones keeps the app on one icon family.
        icons={{ CopyIcon: Copy, CheckIcon: Check }}
        translations={translations}
        components={components}
        // Answers may mix Turkish and English; per-block detection beats forcing
        // one direction on the whole message.
        dir="auto"
      >
        {source}
      </Streamdown>
    </MarkdownScope>
    </MarkdownTableProvider>
  );
}

export default AgentMarkdown;
