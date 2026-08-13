"use client";

import { useState } from "react";

import { ResetPasswordPage } from "@/components/ui/reset-password";
import { ThemeToggleIcon } from "@/components/ui/ThemeToggleIcon";
import { useRouter } from "@/i18n/navigation";
import { api, ApiError } from "@/lib/api";
import { HERO_IMAGE, TESTIMONIALS } from "@/lib/auth-screen";

/**
 * The password-reset screen. Same design as /login and /signup, different
 * fields.
 *
 * `"use client"` lives here rather than in the component, matching the other
 * two auth pages, so `components/ui/` stays consistent with sign-in.tsx —
 * which is kept as supplied.
 */
export default function ResetPasswordRoute() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  async function handleReset(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError(null);

    // The component already disables the button on a mismatch; this is the
    // second line of defence, same reasoning as signup's — a disabled button
    // is a UI state, not a guarantee.
    if (data.get("password") !== data.get("confirmPassword")) {
      setError("Passwords do not match.");
      return;
    }

    try {
      // The API replies the same way whether or not the email has an
      // account, so there's nothing to branch on here beyond "the request
      // succeeded" — sending straight to login is the only honest next step.
      await api.resetPassword({
        email: String(data.get("email")),
        new_password: String(data.get("password")),
      });
      router.replace("/login?reset=1");
    } catch (err) {
      if (err instanceof ApiError && err.isRefusal) {
        setError(err.message);
      } else {
        setError("Could not reset the password. Please try again.");
      }
    }
  }

  return (
    <div className="bg-background text-foreground">
      <ThemeToggleIcon className="fixed top-4 left-4 z-50" />

      <ResetPasswordPage
        heroImageSrc={HERO_IMAGE}
        testimonials={TESTIMONIALS}
        onReset={handleReset}
        onSignIn={() => router.push("/login")}
      />
      {error && (
        <p
          role="alert"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded-2xl border border-border bg-card px-5 py-3 text-sm text-destructive shadow-lg"
        >
          {error}
        </p>
      )}
    </div>
  );
}
