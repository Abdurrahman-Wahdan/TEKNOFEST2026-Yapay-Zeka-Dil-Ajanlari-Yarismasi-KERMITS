// @mui material components
import Tooltip from "@mui/material/Tooltip";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";
import VuiAvatar from "components/VuiAvatar";
import VuiProgress from "components/VuiProgress";

// Images
import AdobeXD from "examples/Icons/AdobeXD";
import Atlassian from "examples/Icons/Atlassian";
import Slack from "examples/Icons/Slack";
import Spotify from "examples/Icons/Spotify";
import Jira from "examples/Icons/Jira";
import Invision from "examples/Icons/Invision";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoSlack = "/vision/images/small-logos/logo-slack.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoSpotify = "/vision/images/small-logos/logo-spotify.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoJira = "/vision/images/small-logos/logo-jira.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoInvesion = "/vision/images/small-logos/logo-invision.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const avatar1 = "/vision/images/avatar1.png";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const avatar2 = "/vision/images/avatar2.png";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const avatar3 = "/vision/images/avatar3.png";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const avatar4 = "/vision/images/avatar4.png";
export default function data() {
  const avatars = (members) =>
    members.map(([image, name]) => (
      <Tooltip key={name} title={name} placeholder="bottom">
        <VuiAvatar
          src={image}
          alt="name"
          size="xs"
          sx={{
            border: ({ borders: { borderWidth }, palette: { dark } }) =>
              `${borderWidth[2]} solid ${dark.focus}`,
            cursor: "pointer",
            position: "relative",

            "&:not(:first-of-type)": {
              ml: -1.25,
            },

            "&:hover, &:focus": {
              zIndex: "10",
            },
          }}
        />
      </Tooltip>
    ));

  return {
    columns: [
      { name: "شركات", align: "left" },
      { name: "أفراد", align: "left" },
      { name: "تبرع", align: "center" },
      { name: "انتهاء", align: "center" },
    ],

    rows: [
      {
        شركات: (
          <VuiBox display="flex" alignItems="center">
            <AdobeXD size="20px" />
            <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
              إصدار Vision UI XD
            </VuiTypography>
          </VuiBox>
        ),
        أفراد: (
          <VuiBox display="flex" py={1}>
            {avatars([
              [avatar1, "Ryan Tompson"],
              [avatar2, "Romina Hadid"],
              [avatar3, "Alexander Smith"],
              [avatar4, "Jessica Doe"],
            ])}
          </VuiBox>
        ),
        تبرع: (
          <VuiTypography variant="button" color="white" fontWeight="bold">
            $14,000
          </VuiTypography>
        ),
        انتهاء: (
          <VuiBox width="8rem" textAlign="left">
            <VuiTypography color="white" variant="button" fontWeight="bold">
              100%
            </VuiTypography>
            <VuiProgress value={60} color="info" label={false} sx={{ background: "#242628" }} />
          </VuiBox>
        ),
      },
      {
        شركات: (
          <VuiBox display="flex" alignItems="center">
            <Atlassian size="20px" />
            <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
              إضافة مسار التقدم
            </VuiTypography>
          </VuiBox>
        ),
        أفراد: (
          <VuiBox display="flex" py={1}>
            {avatars([
              [avatar2, "Romina Hadid"],
              [avatar4, "Jessica Doe"],
            ])}
          </VuiBox>
        ),
        تبرع: (
          <VuiTypography variant="button" color="white" fontWeight="bold">
            $3,000
          </VuiTypography>
        ),
        انتهاء: (
          <VuiBox width="8rem" textAlign="left">
            <VuiTypography color="white" variant="button" fontWeight="bold">
              100%
            </VuiTypography>
            <VuiProgress value={10} color="info" label={false} sx={{ background: "#242628" }} />
          </VuiBox>
        ),
      },
      {
        شركات: (
          <VuiBox display="flex" alignItems="center">
            <Slack size="20px" />
            <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
              إصلاح أخطاء النظام الأساسي
            </VuiTypography>
          </VuiBox>
        ),
        أفراد: (
          <VuiBox display="flex" py={1}>
            {avatars([
              [avatar1, "Ryan Tompson"],
              [avatar3, "Alexander Smith"],
            ])}
          </VuiBox>
        ),
        تبرع: (
          <VuiTypography variant="button" color="white" fontWeight="bold">
            غير مضبوط
          </VuiTypography>
        ),
        انتهاء: (
          <VuiBox width="8rem" textAlign="left">
            <VuiTypography color="white" variant="button" fontWeight="bold">
              100%
            </VuiTypography>
            <VuiProgress value={100} color="info" label={false} sx={{ background: "#242628" }} />
          </VuiBox>
        ),
      },
      {
        شركات: (
          <VuiBox display="flex" alignItems="center">
            <Spotify size="20px" />
            <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
              إطلاق تطبيق الهاتف المحمول الخاص بنا
            </VuiTypography>
          </VuiBox>
        ),
        أفراد: (
          <VuiBox display="flex" py={1}>
            {avatars([
              [avatar4, "Jessica Doe"],
              [avatar3, "Alexander Smith"],
              [avatar2, "Romina Hadid"],
              [avatar1, "Ryan Tompson"],
            ])}
          </VuiBox>
        ),
        تبرع: (
          <VuiTypography variant="button" color="white" fontWeight="bold">
            $20,500
          </VuiTypography>
        ),
        انتهاء: (
          <VuiBox width="8rem" textAlign="left">
            <VuiTypography color="white" variant="button" fontWeight="bold">
              100%
            </VuiTypography>
            <VuiProgress value={100} color="info" label={false} sx={{ background: "#242628" }} />
          </VuiBox>
        ),
      },
      {
        شركات: (
          <VuiBox display="flex" alignItems="center">
            <Jira size="20px" />
            <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
              أضف صفحة الأسعار الجديدة
            </VuiTypography>
          </VuiBox>
        ),
        أفراد: (
          <VuiBox display="flex" py={1}>
            {avatars([[avatar4, "Jessica Doe"]])}
          </VuiBox>
        ),
        تبرع: (
          <VuiTypography variant="button" color="white" fontWeight="bold">
            $500
          </VuiTypography>
        ),
        انتهاء: (
          <VuiBox width="8rem" textAlign="left">
            <VuiTypography color="white" variant="button" fontWeight="bold">
              100%
            </VuiTypography>
            <VuiProgress value={25} color="info" label={false} sx={{ background: "#242628" }} />
          </VuiBox>
        ),
      },
      {
        شركات: (
          <VuiBox display="flex" alignItems="center">
            <Invision size="20px" />
            <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
              إعادة تصميم متجر جديد على الإنترنت
            </VuiTypography>
          </VuiBox>
        ),
        أفراد: (
          <VuiBox display="flex" py={1}>
            {avatars([
              [avatar1, "Ryan Tompson"],
              [avatar4, "Jessica Doe"],
            ])}
          </VuiBox>
        ),
        تبرع: (
          <VuiTypography variant="button" color="white" fontWeight="bold">
            $2,000
          </VuiTypography>
        ),
        انتهاء: (
          <VuiBox width="8rem" textAlign="left">
            <VuiTypography color="white" variant="button" fontWeight="bold">
              100%
            </VuiTypography>
            <VuiProgress value={40} color="info" label={false} sx={{ background: "#242628" }} />
          </VuiBox>
        ),
      },
    ],
  };
}
