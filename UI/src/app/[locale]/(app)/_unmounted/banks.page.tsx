import Card from "@mui/material/Card";
import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppPage } from "@/components/layout/AppPage";
import { VuiBox, VuiTypography } from "@/components/vision";
import { BankRegistry } from "@/components/widgets/BankRegistry";

export default async function BanksPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("banks");

  return (
    <AppPage>
      <Card>
        <VuiBox mb="22px">
          <VuiTypography variant="lg" color="white">
            {t("title")}
          </VuiTypography>
        </VuiBox>
        <BankRegistry />
      </Card>
    </AppPage>
  );
}
