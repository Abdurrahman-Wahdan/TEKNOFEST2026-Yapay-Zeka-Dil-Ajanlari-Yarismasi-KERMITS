"use client";

import { useLocale } from "next-intl";
import { useState } from "react";

import { SignUpPage } from "@/components/ui/sign-up";
import { ThemeToggleIcon } from "@/components/ui/ThemeToggleIcon";
import { useRouter } from "@/i18n/navigation";
import { api, ApiError } from "@/lib/api";
import { HERO_IMAGE, TESTIMONIALS } from "@/lib/auth-screen";

/**
 * The account-creation screen. Same design as /login, different fields.
 *
 * `"use client"` lives here rather than in the component, so `components/ui/`
 * stays consistent with sign-in.tsx — which is kept as supplied.
 */
export default function SignupPage() {
  const router = useRouter();
  const locale = useLocale() as "tr" | "en";
  const [error, setError] = useState<string | null>(null);

  async function handleSignUp(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError(null);

    // The component already disables the button on a mismatch; this is the
    // second line of defence, because a disabled button is a UI state and not
    // a guarantee — requestSubmit() and autofill can both get past it.
    if (data.get("password") !== data.get("confirmPassword")) {
      setError("Passwords do not match.");
      return;
    }

    try {
      // Created, not signed in: the account exists in the database the
      // moment this call succeeds, but no session is established from it —
      // the tokens the API returns are discarded, and the user signs in for
      // themselves on the next screen instead of landing in the app on a
      // session they never asked to start.
      await api.signup({
        email: String(data.get("email")),
        password: String(data.get("password")),
        display_name: String(data.get("name") ?? ""),
        locale,
      });
      router.replace("/login?created=1");
    } catch (err) {
      // The API distinguishes "this email is taken" (409) from "this password
      // is too short" (422), and so does the message — a single "signup failed"
      // leaves the user guessing which field to change.
      if (err instanceof ApiError && err.status === 409) {
        setError("An account with this email already exists.");
      } else if (err instanceof ApiError && err.isRefusal) {
        setError(err.message);
      } else {
        setError("Could not create the account. Please try again.");
      }
    }
  }

  return (
    <div className="bg-background text-foreground">
      <ThemeToggleIcon className="fixed top-4 left-4 z-50" />

      <SignUpPage
        heroImageSrc={HERO_IMAGE}
        testimonials={TESTIMONIALS}
        onSignUp={handleSignUp}
        showGoogleSignUp={false}
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
