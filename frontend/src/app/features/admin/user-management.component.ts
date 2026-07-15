import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/services/api.service';
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
  protected statusMessage = 'Učitavanje korisnika...';
  protected resetLinkByUsername: Record<string, string> = {};

  constructor(private readonly api: ApiService) {}

  ngOnInit(): void {
    this.loadUsers();
  }

  loadUsers(): void {
    this.isLoading = true;
    this.api.listUsers().subscribe({
      next: (response) => {
        this.users = response.users;
        this.isLoading = false;
        this.statusMessage = `${this.users.length} korisnik(a) u sistemu.`;
      },
      error: () => {
        this.isLoading = false;
        this.statusMessage = 'Neuspešno učitavanje korisnika.';
      },
    });
  }

  createUser(): void {
    const username = this.newUsername.trim();
    if (!username || this.newPassword.length < 6) {
      this.statusMessage = 'Korisničko ime je obavezno, a lozinka mora imati bar 6 karaktera.';
      return;
    }

    this.isCreating = true;
    this.api.createUser({ username, password: this.newPassword, role: this.newRole }).subscribe({
      next: () => {
        this.isCreating = false;
        this.newUsername = '';
        this.newPassword = '';
        this.newRole = 'analyst';
        this.statusMessage = `Korisnik "${username}" je kreiran.`;
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
        this.statusMessage = `Nalog "${user.username}" je sada ${nextStatus === 'active' ? 'aktivan' : 'blokiran'}.`;
        this.loadUsers();
      },
      error: () => {
        this.statusMessage = `Neuspešna promena statusa za "${user.username}".`;
      },
    });
  }

  generateResetLink(user: AuthUser): void {
    this.api.generateResetLink(user.id).subscribe({
      next: (response) => {
        this.resetLinkByUsername = { ...this.resetLinkByUsername, [user.username]: response.reset_link };
        this.statusMessage = `Link za resetovanje generisan za "${user.username}". Prosledite ga korisniku.`;
      },
      error: () => {
        this.statusMessage = `Neuspešno generisanje linka za "${user.username}".`;
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
    return 'Kreiranje korisnika nije uspelo.';
  }
}
