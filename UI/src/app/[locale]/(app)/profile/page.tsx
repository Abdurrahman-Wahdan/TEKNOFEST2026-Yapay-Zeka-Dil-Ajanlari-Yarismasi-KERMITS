"use client";

import Page from "layouts/profile";

import { RequireAuth } from "@/components/layout/RequireAuth";

/** Vision UI's profile page, rendered unchanged inside the Vision UI shell. */
export default function ProfileRoute() {
  return (
    <RequireAuth>
      <Page />
    </RequireAuth>
  );
}
