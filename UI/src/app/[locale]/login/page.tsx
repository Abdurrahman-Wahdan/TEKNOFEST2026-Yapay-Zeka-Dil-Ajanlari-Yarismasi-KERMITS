"use client";

import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { SignInPage } from "@/components/ui/sign-in";
import { ThemeToggleIcon } from "@/components/ui/ThemeToggleIcon";
import { useRouter } from "@/i18n/navigation";
import { HERO_IMAGE, TESTIMONIALS } from "@/lib/auth-screen";
import { useAuth } from "@/lib/auth";

/**
 * The login screen.
 *
 * `"use client"` lives here rather than in `components/ui/sign-in.tsx` so that
 * file stays byte-for-byte as supplied — a client page makes everything it
 * imports client-side, which is what the `useState` inside the component needs.
 */
export default function LoginPage() {
  const t = useTranslations("auth");
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  // Signup no longer signs a user in — it sends them here instead, so this is
  // the one place that tells them the account they just created is ready.
  const justCreated = searchParams.get("created") === "1";
  // Same idea for a password reset: it never signs the user in either, it
  // just tells them what to do next.
  const justReset = searchParams.get("reset") === "1";
  const [error, setError] = useState<string | null>(null);

  async function handleSignIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError(null);
    try {
      // An unchecked checkbox is simply absent from FormData, not "off" —
      // `get` returns null, which is why this is a presence check rather
      // than a value comparison.
      const remember = data.get("rememberMe") !== null;
      await login(String(data.get("email")), String(data.get("password")), remember);
      // /compare: both /dashboard and /finansman are unmounted (see
      // `src/app/[locale]/(app)/_unmounted/README.md`), and Karşılaştır is now
      // the first live page in the drawer. Keep this in step with the root
      // redirect in `src/app/[locale]/page.tsx`.
      router.replace("/compare");
    } catch {
      // One message for a wrong password and for an unknown address, matching
      // what the API returns — telling them apart would confirm which accounts
      // exist.
      setError("Incorrect email or password.");
    }
  }

  return (
    <div className="bg-background text-foreground">
      {/*
        Overlaid rather than passed into SignInPage: that component is kept
        byte-for-byte as supplied and takes no slot for extra controls.

        Top-left, over the form column — the right half is the hero image, and
        a control sitting on a photograph is both harder to read and reads as
        part of the picture rather than part of the app.
      */}
      <ThemeToggleIcon className="fixed top-4 left-4 z-50" />

      <SignInPage
        heroImageSrc={HERO_IMAGE}
        testimonials={TESTIMONIALS}
        onSignIn={handleSignIn}
        showGoogleSignIn={false}
        onResetPassword={() => router.push("/reset-password")}
        onCreateAccount={() => router.push("/signup")}
      />
      {error ? (
        <p
          role="alert"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded-2xl border border-border bg-card px-5 py-3 text-sm text-destructive shadow-lg"
        >
          {error}
        </p>
      ) : (
        (justCreated || justReset) && (
          <p
            role="status"
            className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded-2xl border border-border bg-card px-5 py-3 text-sm text-[var(--ok)] shadow-lg"
          >
            {justCreated ? t("accountCreated") : t("passwordWasReset")}
          </p>
        )
      )}
    </div>
  );
}
