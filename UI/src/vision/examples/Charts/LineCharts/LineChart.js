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

import React from "react";
import dynamic from "next/dynamic";

// Loaded client-side only: apexcharts reaches for `window` at module scope, so
// importing it directly crashes server rendering. A "use client" page is still
// rendered on the server, so the page directive alone is not enough.
const ReactApexChart = dynamic(() => import("react-apexcharts"), { ssr: false });

class LineChart extends React.Component {
  constructor(props) {
    super(props);

    // Seeded from props rather than left empty until componentDidMount.
    // ReactApexChart is loaded through next/dynamic, so it mounts at an
    // unpredictable point after the import resolves — and if it lands on the
    // empty initial state it draws axes with no series and never recovers.
    // Under CRA the import was synchronous, so this never showed.
    this.state = {
      chartData: props.lineChartData || [],
      chartOptions: props.lineChartOptions || {},
    };
  }

  componentDidMount() {
    const { lineChartData, lineChartOptions } = this.props;

    this.setState({
      chartData: lineChartData,
      chartOptions: lineChartOptions,
    });
  }

  render() {
    return (
      <ReactApexChart
        options={this.state.chartOptions}
        series={this.state.chartData}
        type="area"
        width="100%"
        height="100%"
      />
    );
  }
}

export default LineChart;
