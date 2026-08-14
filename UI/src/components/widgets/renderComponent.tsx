"use client";

import { Component as ReactComponent, type ComponentType, type ReactNode } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";
import type { ComponentSpec } from "@/lib/contract";

import { CATALOG, knownWidgetTypes } from "./catalog";

/**
 * Turn one `{type, props}` into something on screen — or into a visible,
 * specific account of why it could not be.
 *
 * The API stores produced components without validating them, which makes this
 * the only place the contract is enforced. That has one consequence worth being
 * explicit about: **every failure here is a failure the user sees**. A widget
 * that silently vanished would leave the producer's page looking merely short,
 * and nobody — not the user, not us, not whoever is debugging the producer —
 * would know a component had been dropped.
 */
export function renderComponent(spec: ComponentSpec, index: number): ReactNode {
  const entry = CATALOG[spec.type];

  if (!entry) {
    return (
      <Problem tone="warning" title={`Bilinmeyen bileşen: ${spec.type}`}>
        <VuiTypography variant="caption" color="text">
          Tanımlı bileşenler: {knownWidgetTypes().join(", ")}.
        </VuiTypography>
        <RawProps props={spec.props} />
      </Problem>
    );
  }

  const parsed = entry.props.safeParse(spec.props);
  if (!parsed.success) {
    return (
      <Problem tone="error" title={`${spec.type} beklenen veriyi almadı`}>
        <VuiBox component="ul" pl={2} sx={{ listStyle: "disc" }}>
          {parsed.error.issues.map((issue) => {
            const path = issue.path.length > 0 ? issue.path.join(".") : "(kök)";
            return (
              <VuiBox component="li" key={`${path}-${issue.message}`}>
                <VuiTypography variant="caption" color="text">
                  {path}: {issue.message}
                </VuiTypography>
              </VuiBox>
            );
          })}
        </VuiBox>
        <RawProps props={spec.props} />
      </Problem>
    );
  }

  const Widget = entry.component as ComponentType<Record<string, unknown>>;
  return (
    <WidgetBoundary type={spec.type} key={`${spec.type}-${index}`}>
      <Widget {...(parsed.data as Record<string, unknown>)} />
    </WidgetBoundary>
  );
}

function Problem({
  tone,
  title,
  children,
}: {
  tone: "warning" | "error";
  title: string;
  children: ReactNode;
}) {
  // Two different problems, two different colours: "we have never heard of this
  // component" and "we know it but the data was wrong" need different fixes, in
  // different places.
  const accent = tone === "warning" ? "warning.main" : "error.main";

  return (
    <VuiBox
      p={2}
      borderRadius="lg"
      sx={{
        border: `1px dashed`,
        borderColor: accent,
        background: "rgba(255, 255, 255, 0.03)",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
      }}
    >
      <VuiTypography variant="button" color="white" fontWeight="medium">
        {title}
      </VuiTypography>
      {children}
    </VuiBox>
  );
}

/** The payload, one disclosure away. Debugging the producer needs the real thing. */
function RawProps({ props }: { props: unknown }) {
  return (
    <VuiBox component="details">
      <VuiBox component="summary" sx={{ cursor: "pointer" }}>
        <VuiTypography variant="caption" color="text">
          Gelen veri
        </VuiTypography>
      </VuiBox>
      <VuiBox
        component="pre"
        mt={1}
        p={1.5}
        borderRadius="lg"
        sx={{
          background: "rgba(0, 0, 0, 0.35)",
          maxHeight: "14rem",
          overflow: "auto",
          fontSize: "11px",
          lineHeight: 1.5,
          color: "#cbd5f5",
        }}
      >
        {safeStringify(props)}
      </VuiBox>
    </VuiBox>
  );
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    // Circular, a BigInt, or something else JSON refuses. The point of this
    // block is to show *something*, so failing to render the failure is the
    // one outcome not allowed.
    return String(value);
  }
}

/**
 * Keeps one misbehaving widget from taking the page with it.
 *
 * Error boundaries still have to be class components in React 19 — there is no
 * hook equivalent.
 */
class WidgetBoundary extends ReactComponent<
  { type: string; children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`Widget "${this.props.type}" threw while rendering.`, error);
  }

  render() {
    if (this.state.error) {
      return (
        <Problem tone="error" title={`${this.props.type} gösterilirken hata oluştu`}>
          <VuiTypography variant="caption" color="text">
            {this.state.error.message}
          </VuiTypography>
        </Problem>
      );
    }
    return this.props.children;
  }
}
