import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { AuthUser, UserStatus } from '../../core/models/shared.models';
import { CreateUserRequest, ResetLinkResponse } from '../../features/admin/admin.models';

@Injectable({
  providedIn: 'root',
})
export class AdminApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  listUsers(search?: string | null): Observable<{ users: AuthUser[] }> {
    const trimmed = search?.trim();
    const params = trimmed ? new HttpParams().set('search', trimmed) : undefined;
    return this.http.get<{ users: AuthUser[] }>(`${this.apiUrl}/api/v1/users`, { params });
  }

  createUser(request: CreateUserRequest): Observable<AuthUser> {
    return this.http.post<AuthUser>(`${this.apiUrl}/api/v1/users`, request);
  }

  setUserStatus(userId: string, status: UserStatus): Observable<AuthUser> {
    return this.http.patch<AuthUser>(`${this.apiUrl}/api/v1/users/${userId}/status`, { status });
  }

  generateResetLink(userId: string): Observable<ResetLinkResponse> {
    return this.http.post<ResetLinkResponse>(`${this.apiUrl}/api/v1/users/${userId}/reset-link`, {});
  }

  renameUser(userId: string, username: string): Observable<AuthUser> {
    return this.http.patch<AuthUser>(`${this.apiUrl}/api/v1/users/${userId}`, { username });
  }

  deleteUser(userId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/users/${userId}`);
  }
}
