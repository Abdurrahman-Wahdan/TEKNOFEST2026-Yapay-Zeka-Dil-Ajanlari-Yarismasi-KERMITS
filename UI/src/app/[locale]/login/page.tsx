"use client";

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
  const { login } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  async function handleSignIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError(null);
    try {
      await login(String(data.get("email")), String(data.get("password")));
      router.replace("/dashboard");
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
        onGoogleSignIn={() => setError("Google sign-in is not configured.")}
        onResetPassword={() => setError("Password reset is not built yet.")}
        onCreateAccount={() => router.push("/signup")}
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
