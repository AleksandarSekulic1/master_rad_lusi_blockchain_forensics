import { Routes } from '@angular/router';

export const appRoutes: Routes = [
	{
		path: '',
		pathMatch: 'full',
		redirectTo: 'dashboard',
	},
	{
		path: 'dashboard',
		loadComponent: () =>
			import('./features/dashboard/dashboard.component').then((module) => module.DashboardComponent),
	},
	{
		path: 'graph',
		loadComponent: () =>
			import('./features/graph-visualization/graph-visualization.component').then((module) => module.GraphVisualizationComponent),
	},
	{
		path: 'reports',
		loadComponent: () =>
			import('./features/report-export/report-export.component').then((module) => module.ReportExportComponent),
	},
	{
		path: '**',
		redirectTo: 'dashboard',
	},
];
