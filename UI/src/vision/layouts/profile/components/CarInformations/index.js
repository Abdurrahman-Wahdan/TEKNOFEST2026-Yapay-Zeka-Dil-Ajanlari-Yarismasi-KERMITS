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

// @mui material components
import { useTheme } from "@mui/material/styles";

import React from 'react';
import { Card, Stack, Grid } from '@mui/material';
import VuiBox from 'components/VuiBox';
import VuiTypography from 'components/VuiTypography';
// Served from /public — see the note on the other image constants.
const GreenLightning = "/vision/images/shapes/green-lightning.svg";
// Served from /public — see the note on the other image constants.
const WhiteLightning = "/vision/images/shapes/white-lightning.svg";
import linearGradient from 'assets/theme/functions/linearGradient';
// Served from /public — see the note on the other image constants.
const carProfile = "/vision/images/shapes/car-profile.svg";
import LineChart from 'examples/Charts/LineCharts/LineChart';
import { lineChartDataProfile1, lineChartDataProfile2 } from 'variables/charts';
import { lineChartOptionsProfile2, lineChartOptionsProfile1 } from 'variables/charts';
import CircularProgress from '@mui/material/CircularProgress';
const CarInformations = ({ name = "Mark Johnson" }) => {
	const { gradients, info } = useTheme().palette;
	const { cardContent } = gradients;
	return (
		<Card
			sx={({ breakpoints }) => ({
				[breakpoints.up('xxl')]: {
					maxHeight: '400px'
				}
			})}>
			<VuiBox display='flex' flexDirection='column'>
				<VuiTypography variant='lg' color='white' fontWeight='bold' mb='6px'>
					Car Informations
				</VuiTypography>
				<VuiTypography variant='button' color='text' fontWeight='regular' mb='30px'>
					Hello, {name}! Your Car is ready.
				</VuiTypography>
				<Stack
					spacing='24px'
					background='#fff'
					sx={({ breakpoints }) => ({
						[breakpoints.up('sm')]: {
							flexDirection: 'column'
						},
						[breakpoints.up('md')]: {
							flexDirection: 'row'
						},
						[breakpoints.only('xl')]: {
							flexDirection: 'column'
						}
					})}>
					<VuiBox
						display='flex'
						flexDirection='column'
						justifyContent='center'
						sx={({ breakpoints }) => ({
							[breakpoints.only('sm')]: {
								alignItems: 'center'
							}
						})}
						alignItems='center'>
						<VuiBox sx={{ position: 'relative', display: 'inline-flex' }}>
							<CircularProgress variant='determinate' value={68} size={170} color='info' />
							{/*
								The label has to be an absolute overlay, or inline-flex lays it out
								*beside* the ring instead of inside it — which is how the template
								ships it. `SatisfactionRate` on the dashboard does it correctly, and
								this matches that: same inset-0 flex-centre box.
							*/}
							<VuiBox
								sx={{
									top: 0,
									left: 0,
									bottom: 0,
									right: 0,
									position: 'absolute',
									display: 'flex',
									flexDirection: 'column',
									alignItems: 'center',
									justifyContent: 'center'
								}}>
								<VuiBox component='img' src={GreenLightning} />
								<VuiTypography color='white' variant='h2' mt='6px' fontWeight='bold' mb='4px'>
									68%
								</VuiTypography>
								<VuiTypography color='text' variant='caption'>
									Current Load
								</VuiTypography>
							</VuiBox>
						</VuiBox>
						<VuiBox
							display='flex'
							justifyContent='center'
							flexDirection='column'
							sx={{ textAlign: 'center' }}>
							<VuiTypography color='white' variant='lg' fontWeight='bold' mb='2px' mt='18px'>
								0h 58 min
							</VuiTypography>
							<VuiTypography color='text' variant='button' fontWeight='regular'>
								Time to full charge
							</VuiTypography>
						</VuiBox>
					</VuiBox>
					<Grid
						container
						sx={({ breakpoints }) => ({
							spacing: '24px',
							[breakpoints.only('sm')]: {
								columnGap: '0px',
								rowGap: '24px'
							},
							[breakpoints.up('md')]: {
								gap: '24px',
								ml: '50px !important'
							},
							[breakpoints.only('xl')]: {
								gap: '12px',
								mx: 'auto !important'
							}
						})}>
						<Grid item xs={12} md={5.5} xl={5.8} xxl={5.5}>
							<VuiBox
								display='flex'
								p='18px'
								alignItems='center'
								sx={{
									background: linearGradient(cardContent.main, cardContent.state, cardContent.deg),
									minHeight: '110px',
									borderRadius: '20px'
								}}>
								<VuiBox display='flex' flexDirection='column' mr='auto'>
									<VuiTypography color='text' variant='caption' fontWeight='medium' mb='2px'>
										Battery Health
									</VuiTypography>
									<VuiTypography
										color='white'
										variant='h4'
										fontWeight='bold'
										sx={({ breakpoints }) => ({
											[breakpoints.only('xl')]: {
												fontSize: '20px'
											}
										})}>
										76%
									</VuiTypography>
								</VuiBox>
								<VuiBox
									display='flex'
									justifyContent='center'
									alignItems='center'
									sx={{
										background: info.main,
										borderRadius: '12px',
										width: '56px',
										height: '56px'
									}}>
									<VuiBox component='img' src={carProfile} />
								</VuiBox>
							</VuiBox>
						</Grid>
						<Grid item xs={12} md={5.5} xl={5.8} xxl={5.5}>
							<VuiBox
								display='flex'
								p='18px'
								alignItems='center'
								sx={{
									background: linearGradient(cardContent.main, cardContent.state, cardContent.deg),
									borderRadius: '20px'
								}}>
								<VuiBox display='flex' flexDirection='column' mr='auto'>
									<VuiTypography color='text' variant='caption' fontWeight='medium' mb='2px'>
										Efficiency
									</VuiTypography>
									<VuiTypography
										color='white'
										variant='h4'
										fontWeight='bold'
										sx={({ breakpoints }) => ({
											[breakpoints.only('xl')]: {
												fontSize: '20px'
											}
										})}>
										+20%
									</VuiTypography>
								</VuiBox>
								<VuiBox sx={{ maxHeight: '75px' }}>
									<LineChart
										lineChartData={lineChartDataProfile1}
										lineChartOptions={lineChartOptionsProfile1}
									/>
								</VuiBox>
							</VuiBox>
						</Grid>
						<Grid item xs={12} md={5.5} xl={5.8} xxl={5.5}>
							<VuiBox
								display='flex'
								p='18px'
								alignItems='center'
								sx={{
									background: linearGradient(cardContent.main, cardContent.state, cardContent.deg),
									borderRadius: '20px',
									minHeight: '110px'
								}}>
								<VuiBox display='flex' flexDirection='column' mr='auto'>
									<VuiTypography color='text' variant='caption' fontWeight='medium' mb='2px'>
										Consumption
									</VuiTypography>
									<VuiTypography
										color='white'
										variant='h4'
										fontWeight='bold'
										sx={({ breakpoints }) => ({
											[breakpoints.only('xl')]: {
												fontSize: '20px'
											}
										})}>
										163W/km
									</VuiTypography>
								</VuiBox>
								<VuiBox
									display='flex'
									justifyContent='center'
									alignItems='center'
									sx={{
										background: info.main,
										borderRadius: '12px',
										width: '56px',
										height: '56px'
									}}>
									<VuiBox component='img' src={WhiteLightning} />
								</VuiBox>
							</VuiBox>
						</Grid>
						<Grid item xs={12} md={5.5} xl={5.8} xxl={5.5}>
							<VuiBox
								display='flex'
								p='18px'
								alignItems='center'
								sx={{
									background: linearGradient(cardContent.main, cardContent.state, cardContent.deg),
									borderRadius: '20px'
								}}>
								<VuiBox display='flex' flexDirection='column' mr='auto'>
									<VuiTypography color='text' variant='caption' fontWeight='medium' mb='2px'>
										This Week
									</VuiTypography>
									<VuiTypography
										color='white'
										variant='h4'
										fontWeight='bold'
										sx={({ breakpoints }) => ({
											[breakpoints.only('xl')]: {
												fontSize: '20px'
											}
										})}>
										1.342km
									</VuiTypography>
								</VuiBox>
								<VuiBox sx={{ maxHeight: '75px' }}>
									<LineChart
										lineChartData={lineChartDataProfile2}
										lineChartOptions={lineChartOptionsProfile2}
									/>
								</VuiBox>
							</VuiBox>
						</Grid>
					</Grid>
				</Stack>
			</VuiBox>
		</Card>
	);
};

export default CarInformations;
