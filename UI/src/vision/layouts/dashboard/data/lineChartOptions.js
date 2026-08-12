/*!

=========================================================
* Vision UI Free React - v1.0.0
=========================================================

* Product Page: https://www.creative-tim.com/product/vision-ui-free-react
* Copyright 2021 Creative Tim (https://www.creative-tim.com/)
* Licensed under MIT (https://github.com/creativetimofficial/vision-ui-free-react/blob/master LICENSE.md)

* Design and Coded by Simmmple & Creative Tim

=========================================================

* The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

*/

// Takes the theme's `colors` so the axes, grid and series follow the palette
// and the light/dark toggle.
export const lineChartOptionsDashboard = (colors) => ({
  chart: {
    toolbar: {
      show: false,
    },
  },
  tooltip: {
    theme: colors.chartTooltipTheme,
  },
  dataLabels: {
    enabled: false,
  },
  stroke: {
    curve: "smooth",
  },
  xaxis: {
    // "category", not "datetime": the categories below are month names, which
    // are not parseable dates. ApexCharts throws "invalid Date format" on them
    // and abandons the series — under CRA the throw was swallowed, but through
    // next/dynamic it surfaces as an unhandled rejection and the chart renders
    // axes with no line. Rendered output is identical either way.
    type: "category",
    categories: [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
      "Oct",
      "Nov",
      "Dec",
    ],
    labels: {
      style: {
        colors: colors.text.main,
        fontSize: "10px",
      },
    },
    axisBorder: {
      show: false,
    },
    axisTicks: {
      show: false,
    },
  },
  yaxis: {
    labels: {
      style: {
        colors: colors.text.main,
        fontSize: "10px",
      },
    },
  },
  legend: {
    show: false,
  },
  grid: {
    strokeDashArray: 5,
    borderColor: colors.borderCol.main,
  },
  fill: {
    type: "gradient",
    gradient: {
      shade: colors.chartTooltipTheme,
      type: "vertical",
      shadeIntensity: 0,
      gradientToColors: undefined, // optional, if not defined - uses the shades of same color in series
      inverseColors: true,
      opacityFrom: 0.8,
      opacityTo: 0,
      stops: [],
    },
    colors: [colors.info.main, colors.info.focus],
  },
  colors: [colors.info.main, colors.info.focus],
});
