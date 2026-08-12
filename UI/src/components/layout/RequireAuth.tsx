"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";

import { useRouter } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";

/**
 * Redirects to the login page when nobody is signed in.
 *
 * Deliberately renders nothing while `loading` is true. The session is restored
 * from a stored refresh token in an effect, so a signed-in user is briefly
 * `user: null` on every page load — redirecting on that would bounce them to
 * the login screen every time they refreshed.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const t = useTranslations("common");

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading) return <p>{t("loading")}</p>;
  if (!user) return null;
  return <>{children}</>;
}
