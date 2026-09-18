import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService } from './core/services/auth.service';
import { SettingsService } from './core/services/settings.service';

interface NavItem {
  path: string;
  exact?: boolean;
  adminOnly?: boolean;
  sr: string;
  en: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent {
  readonly navItems: NavItem[] = [
    { path: '/dashboard', exact: true, sr: 'Kontrolna tabla', en: 'Dashboard' },
    { path: '/cases', sr: 'Slučajevi', en: 'Cases' },
    { path: '/graph', sr: 'Graf', en: 'Graph' },
    { path: '/taint', sr: 'Taint analiza', en: 'Taint analysis' },
    { path: '/pathfinding', sr: 'Putanje', en: 'Pathfinding' },
    { path: '/behavioral', sr: 'Ponašanje', en: 'Behavioral analysis' },
    { path: '/dex-swaps', sr: 'DEX razmene', en: 'DEX swaps' },
    { path: '/token-approval', sr: 'Token Approval', en: 'Token Approval' },
    { path: '/sybil-analysis', sr: 'Sybil & Bot mreže', en: 'Sybil & Bot networks' },
    { path: '/verify-report', sr: 'Provera izveštaja', en: 'Verify report' },
    { path: '/activity-log', sr: 'Dnevnik', en: 'Activity log' },
    { path: '/lanac-dokaza', sr: 'Lanac dokaza', en: 'Chain of custody' },
    { path: '/tests', adminOnly: true, sr: 'Testovi', en: 'Tests' },
    { path: '/admin/users', adminOnly: true, sr: 'Administracija', en: 'Administration' },
  ];

  constructor(
    protected readonly auth: AuthService,
    protected readonly settings: SettingsService,
    private readonly router: Router,
  ) {}

  logout(): void {
    this.auth.logout();
    this.router.navigate(['/login']);
  }
}
