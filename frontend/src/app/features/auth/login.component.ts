import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';
import { SettingsService } from '../../core/services/settings.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  protected username = '';
  protected password = '';
  protected isSubmitting = false;
  protected errorMessage: string | null = null;

  constructor(
    private readonly auth: AuthService,
    private readonly router: Router,
    protected readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as every other page's own t()) - the theme/language toggle is now
   * reachable from this page too (see app.component.html), so this page has to react to it. */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  submit(): void {
    if (!this.username.trim() || !this.password) {
      this.errorMessage = this.t('Unesite korisničko ime i lozinku.', 'Enter your username and password.');
      return;
    }

    this.isSubmitting = true;
    this.errorMessage = null;

    this.auth.login({ username: this.username.trim(), password: this.password }).subscribe({
      next: () => {
        this.isSubmitting = false;
        this.router.navigate(['/dashboard']);
      },
      error: (error: unknown) => {
        this.isSubmitting = false;
        this.errorMessage = this.extractErrorMessage(error);
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
    return this.t('Prijava nije uspela. Proverite podatke i pokušajte ponovo.', 'Login failed. Check your credentials and try again.');
  }
}
