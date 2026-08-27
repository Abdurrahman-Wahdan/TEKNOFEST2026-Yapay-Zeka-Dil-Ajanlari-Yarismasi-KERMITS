"use client";

import { Table as MuiTable, TableBody, TableContainer, TableRow } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { Check, Sparkles, TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Link } from "@/i18n/navigation";
import { VuiBox, VuiButton, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";
import {
  tableFromHast,
  type HastNode,
} from "@/lib/chat/markdown-table";
import { conversationForTableMetadata } from "@/lib/chat/table-metadata";
import { slugifyTitle } from "@/lib/saved-view";
import { TABLE_GUTTER, tableRowHoverSx, tableRule } from "@/lib/table-style";

import { useMarkdownTableTools } from "./markdown-table-context";
import { useChat } from "@/lib/chat/ChatProvider";
import { domProps, type El } from "./markdown-dom";

/** Hooked by the wrapper's hover rule below. */
const SAVE_CLASS = "tf26-save-table";

/**
 * The assistant's markdown tables, drawn in the app's table style.
 *
 * These are the overrides handed to Streamdown's `components` prop, so a
 * `| a | b |` in the agent's answer comes out looking like every other table in
 * the app -- MUI table primitives, the shared rule colour, the uppercase
 * micro-headers, the same column gutter -- instead of Streamdown's own
 * Tailwind-prose defaults.
 *
 * A second renderer beside `ProducedTable` rather than a reuse of it, because the
 * two have genuinely different inputs: `ProducedTable` needs typed
 * `ResolvedColumn[]` and a sort handler, and a markdown table has arbitrary
 * string headers, string cells and no types at all. The *style* is shared through
 * `@/lib/table-style`, which is the part that must not drift.
 */

export function MdTable(props: El<"table">) {
  // The scroll container is the table's own, not the message column's. A wide
  // table must not widen the conversation -- that turns every message in the
  // thread into a horizontal scroll.
  return (
    <VuiBox
      my={2}
      // The reveal lives here rather than on the button: the button is two levels
      // down from the table, so a `:hover` on its own parent never fires when the
      // pointer is over the rows. Focus is handled beside it so the control is
      // never hidden from the keyboard.
      sx={{
        [`&:hover .${SAVE_CLASS}, & .${SAVE_CLASS}:focus-visible`]: { opacity: 1 },
      }}
    >
      <SaveToDashboard node={props.node as HastNode | undefined} />
      <TableContainer sx={{ overflowX: "auto" }}>
        <MuiTable {...domProps(props)} />
      </TableContainer>
    </VuiBox>
  );
}

/**
 * Keeps a table the assistant wrote, on the user's own page.
 *
 * The counterpart to the agent's `save_table` tool: that one fires when the user
 * asks for a table, this one when the assistant produced one unprompted and the
 * user decides afterwards that it was worth keeping. Both land as the same
 * `{type: "table", props}` and render through the same widget.
 *
 * **Hidden while the message is still streaming.** Mid-stream the last row is
 * half-parsed, and there is nothing on screen to say the saved copy is short --
 * silent corruption is the one failure worth designing out here.
 */
function SaveToDashboard({ node }: { node: HastNode | undefined }) {
  const t = useTranslations("chat");
  const { streaming } = useMarkdownTableTools();
  const { messages, serverSessionId } = useChat();
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [detail, setDetail] = useState<string>("");

  const props = streaming ? null : tableFromHast(node);
  // No header and no rows: a fragment, and saving it would save nothing.
  if (!props) return null;
  const tableProps = props;

  async function save() {
    setState("saving");
    try {
      if (!serverSessionId) throw new Error(t("saveNeedsConversation"));
      const metadata = await api.tableMetadata({
        session_id: serverSessionId,
        conversation: conversationForTableMetadata(messages),
        table: {
          columns: (tableProps.columns ?? []).map(({ key, label }) => ({ key, label: label ?? "" })),
          rows: tableProps.rows.map(({ cells }) => ({ cells })),
        },
      });
      await api.saveView({
        slug: slugifyTitle(metadata.title),
        title: metadata.title,
        components: [{
          type: "table",
          props: {
            ...tableProps,
            title: metadata.title,
            subtitle: metadata.description,
          },
        }],
        // The same flag the agent's own writes set: this table was composed by
        // the assistant, whoever pressed the button.
        generated: true,
      });
      setState("saved");
    } catch (error) {
      // No toast system in this app, so the failure stays where the action was.
      setDetail(error instanceof Error ? error.message : "");
      setState("error");
    }
  }

  if (state === "saved") {
    return (
      <VuiBox display="flex" alignItems="center" gap="8px" mb={1} className={SAVE_CLASS} sx={{ opacity: 1 }}>
        <Check size={14} />
        <VuiTypography variant="caption" color="text">
          {t("saved")}
        </VuiTypography>
        <VuiTypography
          component={Link}
          href="/ai-overview"
          variant="caption"
          color="info"
          sx={{ textDecoration: "underline" }}
        >
          {t("openDashboard")}
        </VuiTypography>
      </VuiBox>
    );
  }

  return (
    <VuiBox display="flex" alignItems="center" gap="8px" mb={1}>
      <VuiButton
        size="small"
        variant="outlined"
        color={state === "error" ? "error" : "white"}
        disabled={state === "saving"}
        onClick={save}
        className={SAVE_CLASS}
        // Revealed by the wrapper's hover rule, like the attach buttons on a
        // produced table. A save that already failed stays visible, because a
        // control that reports an error and then hides it has not reported it.
        sx={{ opacity: state === "error" ? 1 : 0, transition: "opacity 120ms" }}
      >
        {state === "error" ? (
          <TriangleAlert size={13} style={{ marginRight: 6 }} />
        ) : (
          <Sparkles size={13} style={{ marginRight: 6 }} />
        )}
        {state === "saving" ? t("saving") : state === "error" ? t("saveFailed") : t("saveToDashboard")}
      </VuiButton>
      {state === "error" && detail !== "" && (
        <VuiTypography variant="caption" color="error" title={detail}>
          {detail}
        </VuiTypography>
      )}
    </VuiBox>
  );
}

export function MdThead(props: El<"thead">) {
  return <VuiBox component="thead" {...domProps(props)} />;
}

export function MdTbody(props: El<"tbody">) {
  return <TableBody {...domProps(props)} />;
}

export function MdTr(props: El<"tr">) {
  const theme = useTheme();
  // Applied to header rows too, harmlessly: a `<thead>` row has no `td`, and the
  // hover rule only ever targets `td`.
  return <TableRow sx={tableRowHoverSx(theme)} {...domProps(props)} />;
}

/** Markdown's `|:---|---:|` arrives as an inline text-align, so it is honoured. */
function alignOf(style: React.CSSProperties | undefined) {
  return (style?.textAlign as "left" | "center" | "right") ?? "left";
}

export function MdTh(props: El<"th">) {
  const theme = useTheme();
  const { size, fontWeightBold } = theme.typography;

  return (
    <VuiBox
      component="th"
      pt={1.5}
      pb={1.25}
      textAlign={alignOf(props.style)}
      fontSize={size.xxs}
      fontWeight={fontWeightBold}
      color="text"
      opacity={0.7}
      borderBottom={tableRule(theme)}
      sx={{ whiteSpace: "nowrap", px: TABLE_GUTTER }}
      {...domProps(props)}
    />
  );
}

export function MdTd(props: El<"td">) {
  const theme = useTheme();

  return (
    <VuiBox
      component="td"
      py={1}
      textAlign={alignOf(props.style)}
      borderBottom={tableRule(theme)}
      color="white"
      fontSize={theme.typography.size.sm}
      // Cells wrap, unlike `ProducedTable`'s. That table's cells are figures and
      // labels, which must never break; a markdown cell can hold a sentence, and
      // `nowrap` on a sentence forces the whole table into a horizontal scroll.
      sx={{ px: TABLE_GUTTER, verticalAlign: "top" }}
      {...domProps(props)}
    />
  );
}
