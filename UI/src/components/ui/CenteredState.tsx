"use client";

import type { ReactNode } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";

/**
 * A centred icon and label for an empty, loading or error state.
 *
 * The app has no dedicated empty-state component, and these three states are the
 * same shape in every list it has, so one component covers all of them. Lifted out
 * of `CompareTablesBrowser`, which had it private, when the AI Overview board
 * needed the identical thing — a second copy would have drifted.
 */
export function CenteredState({
  icon,
  label,
  tone = "default",
  children,
}: {
  icon: ReactNode;
  label: string;
  tone?: "default" | "error";
  /** An action under the label — a retry button, typically. */
  children?: ReactNode;
}) {
  return (
    <VuiBox
      display="flex"
      flexDirection="column"
      alignItems="center"
      justifyContent="center"
      gap="10px"
      py="40px"
      color={tone === "error" ? "error" : "text"}
      sx={{ opacity: tone === "error" ? 1 : 0.7 }}
    >
      {icon}
      <VuiTypography variant="button" color={tone === "error" ? "error" : "text"}>
        {label}
      </VuiTypography>
      {children}
    </VuiBox>
  );
}
