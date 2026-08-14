"use client";

import { useTheme } from "@mui/material/styles";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { Pill } from "@/components/ui/Pill";
import { VuiBox, VuiTypography } from "@/components/vision";
import { Link } from "@/i18n/navigation";
import { api } from "@/lib/api";

/**
 * Every bank, what it publishes, and what is currently down.
 *
 * The three states are shown as three different things, because they are: a
 * bank that does not publish an endpoint (a permanent, legitimate answer with a
 * reason), one whose endpoint failed this morning (temporary), and one that is
 * fine. Collapsing them into "unavailable" would tell a user to wait for
 * something that is never coming.
 */
export function BankRegistry() {
  const t = useTranslations("banks");
  const tc = useTranslations("common");
  const { grey } = useTheme().palette;
  const { borderWidth } = useTheme().borders;

  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ["banks"],
    queryFn: api.banks,
  });

  if (isPending) {
    return (
      <VuiTypography variant="button" color="text" fontWeight="regular">
        {tc("loading")}
      </VuiTypography>
    );
  }

  if (isError) {
    return (
      <VuiTypography variant="button" color="text" fontWeight="regular">
        {tc("error")}{" "}
        <VuiTypography
          component="button"
          variant="button"
          color="info"
          onClick={() => refetch()}
          sx={{
            background: "none",
            border: "none",
            cursor: "pointer",
            textDecoration: "underline",
          }}
        >
          {tc("retry")}
        </VuiTypography>
      </VuiTypography>
    );
  }

  return (
    <VuiBox component="ul" sx={{ listStyle: "none", p: 0, m: 0 }}>
      {data.map((bank, index) => (
        <VuiBox
          key={bank.name}
          component="li"
          py={2}
          borderBottom={
            index === data.length - 1
              ? null
              : `${borderWidth[1]} solid ${grey[700]}`
          }
        >
          <VuiBox
            display="flex"
            flexDirection={{ xs: "column", md: "row" }}
            alignItems={{ xs: "flex-start", md: "center" }}
            gap="12px"
          >
            <VuiBox sx={{ minWidth: { md: "14rem" } }}>
              <VuiTypography
                component={Link}
                href={`/banks/${bank.name}`}
                variant="button"
                color="white"
                fontWeight="medium"
              >
                {bank.display_name}
              </VuiTypography>
            </VuiBox>

            <VuiBox display="flex" flexWrap="wrap" gap="6px">
              {bank.publishes.length === 0 && (
                <Pill tone="neutral">{t("noEndpoints")}</Pill>
              )}
              {bank.publishes.map((capability) => (
                // A capability that is published but currently failing is
                // marked rather than hidden — hiding it would look identical
                // to a bank that never offered it.
                <Pill
                  key={capability}
                  tone={
                    (bank.maintenance ?? []).includes(capability) ? "warn" : "ok"
                  }
                >
                  {capability}
                </Pill>
              ))}
            </VuiBox>
          </VuiBox>

          {bank.notes && (
            <VuiBox mt={1}>
              <VuiTypography variant="caption" color="text">
                {bank.notes}
              </VuiTypography>
            </VuiBox>
          )}
        </VuiBox>
      ))}
    </VuiBox>
  );
}

