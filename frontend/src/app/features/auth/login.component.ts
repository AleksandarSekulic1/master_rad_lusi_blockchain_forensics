import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';

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
  ) {}

  submit(): void {
    if (!this.username.trim() || !this.password) {
      this.errorMessage = 'Unesite korisničko ime i lozinku.';
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
    return 'Prijava nije uspela. Proverite podatke i pokušajte ponovo.';
  }
}
