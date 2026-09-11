import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
import { AuthUser, UserRole } from '../../models/blockchain-forensics.models';

@Component({
  selector: 'app-user-management',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './user-management.component.html',
  styleUrl: './user-management.component.scss',
})
export class UserManagementComponent implements OnInit {
  protected users: AuthUser[] = [];
  protected newUsername = '';
  protected newPassword = '';
  protected newRole: UserRole = 'analyst';
  protected isLoading = false;
  protected isCreating = false;
  protected statusMessage = '';
  protected resetLinkByUsername: Record<string, string> = {};

  constructor(
    private readonly api: ApiService,
    protected readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as every other page's own t()). */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.statusMessage = this.t('Učitavanje korisnika...', 'Loading users...');
    this.loadUsers();
  }

  loadUsers(): void {
    this.isLoading = true;
    this.api.listUsers().subscribe({
      next: (response) => {
        this.users = response.users;
        this.isLoading = false;
        this.statusMessage = this.t(`${this.users.length} korisnik(a) u sistemu.`, `${this.users.length} user(s) in the system.`);
      },
      error: () => {
        this.isLoading = false;
        this.statusMessage = this.t('Neuspešno učitavanje korisnika.', 'Failed to load users.');
      },
    });
  }

  createUser(): void {
    const username = this.newUsername.trim();
    if (!username || this.newPassword.length < 6) {
      this.statusMessage = this.t(
        'Korisničko ime je obavezno, a lozinka mora imati bar 6 karaktera.',
        'A username is required, and the password must be at least 6 characters.',
      );
      return;
    }

    this.isCreating = true;
    this.api.createUser({ username, password: this.newPassword, role: this.newRole }).subscribe({
      next: () => {
        this.isCreating = false;
        this.newUsername = '';
        this.newPassword = '';
        this.newRole = 'analyst';
        this.statusMessage = this.t(`Korisnik "${username}" je kreiran.`, `User "${username}" was created.`);
        this.loadUsers();
      },
      error: (error: unknown) => {
        this.isCreating = false;
        this.statusMessage = this.extractErrorMessage(error);
      },
    });
  }

  toggleStatus(user: AuthUser): void {
    const nextStatus = user.status === 'active' ? 'blocked' : 'active';
    this.api.setUserStatus(user.id, nextStatus).subscribe({
      next: () => {
        const statusWord = nextStatus === 'active' ? this.t('aktivan', 'active') : this.t('blokiran', 'blocked');
        this.statusMessage = this.t(`Nalog "${user.username}" je sada ${statusWord}.`, `Account "${user.username}" is now ${statusWord}.`);
        this.loadUsers();
      },
      error: () => {
        this.statusMessage = this.t(`Neuspešna promena statusa za "${user.username}".`, `Failed to change status for "${user.username}".`);
      },
    });
  }

  generateResetLink(user: AuthUser): void {
    this.api.generateResetLink(user.id).subscribe({
      next: (response) => {
        this.resetLinkByUsername = { ...this.resetLinkByUsername, [user.username]: response.reset_link };
        this.statusMessage = this.t(
          `Link za resetovanje generisan za "${user.username}". Prosledite ga korisniku.`,
          `Reset link generated for "${user.username}". Pass it on to the user.`,
        );
      },
      error: () => {
        this.statusMessage = this.t(`Neuspešno generisanje linka za "${user.username}".`, `Failed to generate a link for "${user.username}".`);
      },
    });
  }

  private extractErrorMessage(error: unknown): string {
    if (typeof error === 'object' && error !== null && 'error' in error) {
      const errorObject = error as { error?: { detail?: string } };
      if (errorObject.error?.detail) {
        return errorObject.error.detail;
      }
    }
    return this.t('Kreiranje korisnika nije uspelo.', 'Failed to create the user.');
  }
}
