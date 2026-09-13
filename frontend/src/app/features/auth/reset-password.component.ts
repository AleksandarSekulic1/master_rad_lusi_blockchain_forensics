import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';
import { SettingsService } from '../../core/services/settings.service';

@Component({
  selector: 'app-reset-password',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './reset-password.component.html',
  styleUrl: './reset-password.component.scss',
})
export class ResetPasswordComponent implements OnInit {
  protected token = '';
  protected newPassword = '';
  protected confirmPassword = '';
  protected isSubmitting = false;
  protected errorMessage: string | null = null;
  protected successMessage: string | null = null;

  constructor(
    private readonly route: ActivatedRoute,
    private readonly auth: AuthService,
    private readonly router: Router,
    protected readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as every other page's own t(), including login.component.ts). */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.token = this.route.snapshot.queryParamMap.get('token') ?? '';
    if (!this.token) {
      this.errorMessage = this.t(
        'Link za resetovanje lozinke nije validan. Zatražite novi od administratora.',
        'The password reset link is invalid. Ask an administrator for a new one.',
      );
    }
  }

  submit(): void {
    if (!this.token) {
      return;
    }

    if (this.newPassword.length < 6) {
      this.errorMessage = this.t('Lozinka mora imati bar 6 karaktera.', 'The password must be at least 6 characters.');
      return;
    }

    if (this.newPassword !== this.confirmPassword) {
      this.errorMessage = this.t('Lozinke se ne poklapaju.', 'The passwords do not match.');
      return;
    }

    this.isSubmitting = true;
    this.errorMessage = null;

    this.auth.resetPassword(this.token, this.newPassword).subscribe({
      next: () => {
        this.isSubmitting = false;
        this.successMessage = this.t('Lozinka je uspešno promenjena. Možete se prijaviti.', 'The password was changed successfully. You can now log in.');
        setTimeout(() => this.router.navigate(['/login']), 2000);
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
    return this.t('Resetovanje lozinke nije uspelo.', 'Failed to reset the password.');
  }
}
