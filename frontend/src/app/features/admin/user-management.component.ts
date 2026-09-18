import { CommonModule } from '@angular/common';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';

import { AdminApiService } from './admin.api';
import { SettingsService } from '../../core/services/settings.service';
import { AuthUser, UserRole } from '../../core/models/shared.models';

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

  // --- Detalji reda (klik na korisnika) - preimenovanje i trajno brisanje. Samo jedan red
  // može biti otvoren odjednom, isto kao expandedRows na activity-log, samo ovde je jedan
  // ID umesto Set-a jer se retko otvara više od jednog reda istovremeno. ---
  protected expandedUserId: string | null = null;
  protected editUsername = '';
  protected isRenaming = false;
  protected renameError: string | null = null;
  protected confirmingDeleteId: string | null = null;
  protected isDeleting = false;
  protected deleteError: string | null = null;

  // --- Paginacija - lista korisnika ume da naraste preko jednog ekrana, pa se prikazuje
  // po 5 odjednom. Bez izbora veličine strane (za razliku od activity-log/custody-log) -
  // ova lista je po prirodi mala, jedna fiksna veličina je dovoljna. ---
  protected readonly pageSize = 5;
  protected currentPage = 1;

  // --- Pretraga po korisničkom imenu - server-side (filtrira se u user_management.py, ne
  // ovde), isti obrazac kao Slučajevi (cases.component.ts): debounce pa GET, umesto jednog
  // zahteva po tasteru. ---
  protected searchQuery = '';
  private readonly searchChanges = new Subject<string>();

  constructor(
    private readonly adminApi: AdminApiService,
    protected readonly settings: SettingsService,
    destroyRef: DestroyRef,
  ) {
    this.searchChanges
      .pipe(debounceTime(250), distinctUntilChanged(), takeUntilDestroyed(destroyRef))
      .subscribe((term) => this.fetchUsers(term));
  }

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as every other page's own t()). */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  protected onSearchChange(term: string): void {
    this.searchChanges.next(term);
  }

  protected clearSearch(): void {
    if (!this.searchQuery) {
      return;
    }
    this.searchQuery = '';
    this.fetchUsers('');
  }

  /** "08.06.2026. 09:00" (sr) / "08/06/2026, 09:00" (en) from an ISO timestamp - local
   * time, locale matched to the active language (same pattern as custody-log.component.ts's
   * own formatDateTime()), instead of the raw ISO string the "Kreiran" column used to show. */
  protected formatDateTime(value: string): string {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    const locale = this.settings.lang() === 'sr' ? 'sr-RS' : 'en-GB';
    return parsed.toLocaleString(locale, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  ngOnInit(): void {
    this.statusMessage = this.t('Učitavanje korisnika...', 'Loading users...');
    this.loadUsers();
  }

  /** Re-fetches with the current search term (used by Refresh and after every mutation -
   * rename/delete/block/reset all reload through this, so the search stays applied instead
   * of silently resetting). The debounced search box goes through fetchUsers() directly. */
  loadUsers(): void {
    this.fetchUsers(this.searchQuery);
  }

  private fetchUsers(term: string): void {
    const query = term.trim();
    this.isLoading = true;
    this.adminApi.listUsers(query).subscribe({
      next: (response) => {
        this.users = response.users;
        this.isLoading = false;
        const count = this.users.length;
        if (count === 0) {
          this.statusMessage = query
            ? this.t(`Nema rezultata za "${query}".`, `No results for "${query}".`)
            : this.t('Nema kreiranih korisnika.', 'No users created yet.');
        } else {
          this.statusMessage = query
            ? this.t(`${count} rezultat(a) za "${query}".`, `${count} result(s) for "${query}".`)
            : this.t(`${count} korisnik(a) u sistemu.`, `${count} user(s) in the system.`);
        }
        // Clamp rather than reset to page 1 - a status/reset-link action reloads the list
        // too, and snapping an admin reading page 2 back to page 1 after every click on
        // that page would be worse than just leaving the page number alone when it's
        // still valid. A new search term, though, always starts back at page 1.
        this.currentPage = query !== this.lastSearchedQuery ? 1 : Math.min(this.currentPage, this.totalPages);
        this.lastSearchedQuery = query;
      },
      error: () => {
        this.isLoading = false;
        this.statusMessage = this.t('Neuspešno učitavanje korisnika.', 'Failed to load users.');
      },
    });
  }

  private lastSearchedQuery = '';

  // --- Paginacija -------------------------------------------------------------------------

  protected get totalPages(): number {
    return Math.max(1, Math.ceil(this.users.length / this.pageSize));
  }

  protected get pagedUsers(): AuthUser[] {
    const start = (this.currentPage - 1) * this.pageSize;
    return this.users.slice(start, start + this.pageSize);
  }

  protected goToPage(page: number): void {
    this.currentPage = Math.min(Math.max(1, page), this.totalPages);
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
    this.adminApi.createUser({ username, password: this.newPassword, role: this.newRole }).subscribe({
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
        this.statusMessage = this.extractErrorMessage(error, this.t('Kreiranje korisnika nije uspelo.', 'Failed to create the user.'));
      },
    });
  }

  // --- Detalji reda: preimenovanje i trajno brisanje --------------------------------------

  protected isExpanded(user: AuthUser): boolean {
    return this.expandedUserId === user.id;
  }

  protected toggleUserDetails(user: AuthUser): void {
    if (this.expandedUserId === user.id) {
      this.expandedUserId = null;
      return;
    }
    this.expandedUserId = user.id;
    this.editUsername = user.username;
    this.renameError = null;
    this.confirmingDeleteId = null;
    this.deleteError = null;
  }

  protected renameUser(user: AuthUser): void {
    const username = this.editUsername.trim();
    if (!username || username === user.username) {
      return;
    }

    this.isRenaming = true;
    this.renameError = null;
    this.adminApi.renameUser(user.id, username).subscribe({
      next: () => {
        this.isRenaming = false;
        this.expandedUserId = null;
        this.statusMessage = this.t(`Korisničko ime promenjeno u "${username}".`, `Username changed to "${username}".`);
        this.loadUsers();
      },
      error: (error: unknown) => {
        this.isRenaming = false;
        this.renameError = this.extractErrorMessage(error, this.t('Neuspešna izmena korisničkog imena.', 'Failed to change the username.'));
      },
    });
  }

  /** Delete asks once, inline, before actually calling the API - the action is
   * irreversible (unlike Block, which can be undone with a click), so a stray click
   * shouldn't be able to remove an account outright. */
  protected askDeleteUser(user: AuthUser): void {
    this.confirmingDeleteId = user.id;
    this.deleteError = null;
  }

  protected cancelDeleteUser(): void {
    this.confirmingDeleteId = null;
  }

  protected confirmDeleteUser(user: AuthUser): void {
    this.isDeleting = true;
    this.deleteError = null;
    this.adminApi.deleteUser(user.id).subscribe({
      next: () => {
        this.isDeleting = false;
        this.confirmingDeleteId = null;
        this.expandedUserId = null;
        this.statusMessage = this.t(`Korisnik "${user.username}" je trajno obrisan.`, `User "${user.username}" was permanently deleted.`);
        this.loadUsers();
      },
      error: (error: unknown) => {
        this.isDeleting = false;
        this.deleteError = this.extractErrorMessage(error, this.t('Neuspešno brisanje korisnika.', 'Failed to delete the user.'));
      },
    });
  }

  toggleStatus(user: AuthUser): void {
    const nextStatus = user.status === 'active' ? 'blocked' : 'active';
    this.adminApi.setUserStatus(user.id, nextStatus).subscribe({
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
    this.adminApi.generateResetLink(user.id).subscribe({
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

  private extractErrorMessage(error: unknown, fallback: string): string {
    if (typeof error === 'object' && error !== null && 'error' in error) {
      const errorObject = error as { error?: { detail?: string } };
      if (errorObject.error?.detail) {
        return errorObject.error.detail;
      }
    }
    return fallback;
  }
}
