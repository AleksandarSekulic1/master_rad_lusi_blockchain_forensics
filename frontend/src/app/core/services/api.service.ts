import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  AnalyticsRequest,
  AnalyticsResponse,
  NodeLinkGraphResponse,
  PathFindingRequest,
  PathFindingResponse,
  UploadCsvResponse,
} from '../../models/blockchain-forensics.models';

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  uploadCsv(file: File, user = 'system'): Observable<UploadCsvResponse> {
    const formData = new FormData();
    formData.append('file', file, file.name);
    formData.append('user', user);

    return this.http.post<UploadCsvResponse>(`${this.apiUrl}/api/v1/upload/csv`, formData);
  }

  getGraph(fileName?: string): Observable<NodeLinkGraphResponse> {
    let params = new HttpParams();
    if (fileName) {
      params = params.set('file_name', fileName);
    }

    return this.http.get<NodeLinkGraphResponse>(`${this.apiUrl}/api/v1/graph`, { params });
  }

  runAnalytics(request: AnalyticsRequest = {}): Observable<AnalyticsResponse> {
    return this.http.post<AnalyticsResponse>(`${this.apiUrl}/api/v1/analytics/run`, request);
  }

  findPaths(request: PathFindingRequest): Observable<PathFindingResponse> {
    return this.http.post<PathFindingResponse>(`${this.apiUrl}/api/v1/graph/path-finding`, request);
  }
}