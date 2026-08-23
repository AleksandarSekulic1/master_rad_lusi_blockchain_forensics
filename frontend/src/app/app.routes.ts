import { Routes } from '@angular/router';

import { adminGuard, authGuard, guestGuard } from './core/guards/auth.guard';

export const appRoutes: Routes = [
	{
		path: '',
		pathMatch: 'full',
		redirectTo: 'dashboard',
	},
	{
		path: 'login',
		canActivate: [guestGuard],
		loadComponent: () =>
			import('./features/auth/login.component').then((module) => module.LoginComponent),
	},
	{
		path: 'reset-password',
		loadComponent: () =>
			import('./features/auth/reset-password.component').then((module) => module.ResetPasswordComponent),
	},
	{
		path: 'dashboard',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/dashboard/dashboard.component').then((module) => module.DashboardComponent),
	},
	{
		path: 'cases',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/cases/cases.component').then((module) => module.CasesComponent),
	},
	{
		path: 'graph',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/graph-visualization/graph-visualization.component').then((module) => module.GraphVisualizationComponent),
	},
	{
		path: 'taint',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/taint-analysis/taint-analysis.component').then((module) => module.TaintAnalysisComponent),
	},
	{
		path: 'pathfinding',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/pathfinding/pathfinding.component').then((module) => module.PathfindingComponent),
	},
	{
		path: 'reports',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/report-export/report-export.component').then((module) => module.ReportExportComponent),
	},
	{
		path: 'verify-report',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/report-verification/report-verification.component').then((module) => module.ReportVerificationComponent),
	},
	{
		path: 'activity-log',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/activity-log/activity-log.component').then((module) => module.ActivityLogComponent),
	},
	{
		path: 'lanac-dokaza',
		canActivate: [authGuard],
		loadComponent: () =>
			import('./features/custody-log/custody-log.component').then((module) => module.CustodyLogComponent),
	},
	{
		path: 'tests',
		canActivate: [adminGuard],
		loadComponent: () =>
			import('./features/tests/tests.component').then((module) => module.TestsComponent),
	},
	{
		path: 'admin/users',
		canActivate: [adminGuard],
		loadComponent: () =>
			import('./features/admin/user-management.component').then((module) => module.UserManagementComponent),
	},
	{
		path: '**',
		redirectTo: 'dashboard',
	},
];
