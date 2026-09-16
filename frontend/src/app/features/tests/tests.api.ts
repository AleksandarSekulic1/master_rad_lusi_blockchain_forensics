import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { ScenarioRequest, ScenarioRunResponse, SuiteListResponse, SuiteRunResponse, TestScenario } from '../../features/tests/tests.models';

@Injectable({
  providedIn: 'root',
})
export class TestsApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  // --- Correctness tests (admin only; the backend enforces that, not these methods) ---

  listSuiteTests(): Observable<SuiteListResponse> {
    return this.http.get<SuiteListResponse>(`${this.apiUrl}/api/v1/tests/suite`);
  }

  runSuite(): Observable<SuiteRunResponse> {
    return this.http.post<SuiteRunResponse>(`${this.apiUrl}/api/v1/tests/suite/run`, {});
  }

  listScenarios(): Observable<{ scenarios: TestScenario[] }> {
    return this.http.get<{ scenarios: TestScenario[] }>(`${this.apiUrl}/api/v1/tests/scenarios`);
  }

  createScenario(request: ScenarioRequest): Observable<TestScenario> {
    return this.http.post<TestScenario>(`${this.apiUrl}/api/v1/tests/scenarios`, request);
  }

  updateScenario(scenarioId: string, request: ScenarioRequest): Observable<TestScenario> {
    return this.http.put<TestScenario>(`${this.apiUrl}/api/v1/tests/scenarios/${scenarioId}`, request);
  }

  deleteScenario(scenarioId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/tests/scenarios/${scenarioId}`);
  }

  runScenarios(scenarioId?: string | null): Observable<ScenarioRunResponse> {
    const params = scenarioId ? new HttpParams().set('scenario_id', scenarioId) : undefined;
    return this.http.post<ScenarioRunResponse>(`${this.apiUrl}/api/v1/tests/scenarios/run`, {}, { params });
  }
}
