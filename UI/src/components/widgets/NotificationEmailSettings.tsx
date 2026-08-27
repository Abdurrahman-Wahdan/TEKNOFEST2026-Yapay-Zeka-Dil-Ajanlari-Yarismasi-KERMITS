"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ActionButton } from "@/components/ui/ActionButton";
import { TitleField } from "@/components/widgets/AutomationFields";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";

export function NotificationEmailSettings() {
  const t = useTranslations("settings");
  const settings = useQuery({ queryKey: ["notification-settings"], queryFn: api.notificationSettings });
  const [email, setEmail] = useState("");
  const save = useMutation({
    mutationFn: (value: string) => api.saveNotificationSettings({ notification_email: value || null }),
  });

  if (settings.isPending) return <VuiTypography variant="caption">{t("loading")}</VuiTypography>;
  if (settings.isError) return <VuiTypography variant="caption" sx={{ color: "var(--destructive)" }}>{t("loadFailed")}</VuiTypography>;
  const value = email || settings.data.notification_email || "";
  return (
    <VuiBox display="flex" flexDirection="column" gap="12px">
      <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
        {t("notificationEmailHint", { email: settings.data.account_email })}
      </VuiTypography>
      <TitleField
        type="email"
        value={value}
        placeholder={settings.data.account_email}
        aria-label={t("notificationEmail")}
        onChange={(event) => setEmail(event.target.value)}
      />
      <VuiBox display="flex" alignItems="center" gap="10px">
        <ActionButton onClick={() => save.mutate(value)} disabled={save.isPending}>
        {save.isPending ? t("saving") : t("save")}
        </ActionButton>
        {save.isSuccess && <VuiTypography variant="caption" sx={{ color: "var(--primary-strong)" }}>{t("saved")}</VuiTypography>}
      </VuiBox>
    </VuiBox>
  );
}
