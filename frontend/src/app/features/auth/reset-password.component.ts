import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';

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
  ) {}

  ngOnInit(): void {
    this.token = this.route.snapshot.queryParamMap.get('token') ?? '';
    if (!this.token) {
      this.errorMessage = 'Link za resetovanje lozinke nije validan. Zatražite novi od administratora.';
    }
  }

  submit(): void {
    if (!this.token) {
      return;
    }

    if (this.newPassword.length < 6) {
      this.errorMessage = 'Lozinka mora imati bar 6 karaktera.';
      return;
    }

    if (this.newPassword !== this.confirmPassword) {
      this.errorMessage = 'Lozinke se ne poklapaju.';
      return;
    }

    this.isSubmitting = true;
    this.errorMessage = null;

    this.auth.resetPassword(this.token, this.newPassword).subscribe({
      next: () => {
        this.isSubmitting = false;
        this.successMessage = 'Lozinka je uspešno promenjena. Možete se prijaviti.';
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
    return 'Resetovanje lozinke nije uspelo.';
  }
}
