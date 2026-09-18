import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { AppLang } from '../../core/services/settings.service';
import { ActivityLogResponse, ActivityReportPreview } from '../../features/activity-log/activity-log.models';

/** Filter for the activity report. Empty dates mean "everything since the system started
 * being used"; a single day is dateFrom === dateTo. */
export interface ActivityReportOptions {
  dateFrom?: string | null;
  dateTo?: string | null;
  /** Admin-only; the backend ignores it for everyone else and returns their own entries. */
  users?: string[];
}

@Injectable({
  providedIn: 'root',
})
export class ActivityLogApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** How many records the chosen report filter would produce - drives the disabled state
   * of the generate buttons so an empty report is prevented before the click. */
  getActivityReportPreview(options: ActivityReportOptions): Observable<ActivityReportPreview> {
    return this.http.get<ActivityReportPreview>(`${this.apiUrl}/api/v1/activity-log/report/preview`, {
      params: this.activityReportParams(options),
    });
  }

  /** Plain, unsigned CSV export - raw data for further processing, not a presentation
   * document, so (like the case/transactions CSV exports elsewhere) it needs no signature
   * or verification code. See signActivityReportPdf below for the signed PDF. */
  downloadActivityReportCsv(options: ActivityReportOptions): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/activity-log/report.csv`, {
      params: this.activityReportParams(options),
      responseType: 'blob',
    });
  }

  /** The signed variant: registers the report (verification code + content hash) and
   * builds the PDF entirely server-side, the same as the plain CSV/PDF above - the data
   * has to come from the authoritative log file, not from whatever the page happens to
   * have loaded (see activity_report.py's own docstring). Unlike every other signed
   * report in this app (built client-side with jsPDF), the signature/declaration/language
   * travel TO the server here rather than a verification code traveling back to a
   * client-built PDF. */
  signActivityReportPdf(
    options: ActivityReportOptions,
    signing: { lang: AppLang; declaration: string; signatureImage: string },
  ): Observable<Blob> {
    const body = {
      users: options.users?.length ? options.users : null,
      date_from: options.dateFrom || null,
      date_to: options.dateTo || null,
      tz_offset_minutes: new Date().getTimezoneOffset(),
      lang: signing.lang,
      declaration: signing.declaration,
      signature_image: signing.signatureImage,
    };
    return this.http.post(`${this.apiUrl}/api/v1/activity-log/report/signed.pdf`, body, { responseType: 'blob' });
  }

  /** Recorded analyst actions, newest first. `user` is only honoured for admins - the
   * backend narrows everyone else to their own entries regardless of what's passed, so
   * this parameter is a convenience for the admin filter, not an access control. */
  getActivityLog(options?: { user?: string | null; caseId?: string | null; limit?: number }): Observable<ActivityLogResponse> {
    let params = new HttpParams().set('limit', String(options?.limit ?? 200));
    if (options?.user) {
      params = params.set('user', options.user);
    }
    if (options?.caseId) {
      params = params.set('case_id', options.caseId);
    }
    return this.http.get<ActivityLogResponse>(`${this.apiUrl}/api/v1/activity-log`, { params });
  }

  /** The timezone offset travels with every report request: the server stores UTC but the
   * user picks days as they see them on screen, and the two only line up if the server
   * knows which zone to convert against. */
  private activityReportParams(options: ActivityReportOptions): HttpParams {
    let params = new HttpParams().set('tz_offset_minutes', String(new Date().getTimezoneOffset()));
    if (options.dateFrom) {
      params = params.set('date_from', options.dateFrom);
    }
    if (options.dateTo) {
      params = params.set('date_to', options.dateTo);
    }
    if (options.users?.length) {
      params = params.set('users', options.users.join(','));
    }
    return params;
  }
}
