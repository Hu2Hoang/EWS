import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';

export const API = location.hostname === 'localhost' ? 'http://localhost:8000' : '/api';

export interface CustomerRow {
  id: string; name: string; industry: string; segment: string;
  score: number; level: string; signal_count: number; legacy_flag: boolean;
}
export interface Signal {
  code: string; label: string; detail: Record<string, unknown>; evidence: string[]; policy: string; weight: number;
}
export interface GraphNode { id: string; type: string; label: string; flagged: boolean; props: Record<string, unknown>; }
export interface GraphEdge { source: string; target: string; type: string; }
export interface Context {
  customer: { id: string; name: string; industry: string; segment: string; loan_amount: number;
              default_date: string | null; legacy_flag_date: string | null };
  as_of: string;
  assessment: { signals: Signal[]; signal_count: number; score: number; level: string; legacy_flag: boolean };
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  events: { id: string; type: string; date: string; description: string }[];
  policies: { id: string; title: string; text: string }[];
}
export interface TimelinePoint { month: string; score: number; level: string; signal_count: number; legacy_flag: boolean; }
export interface Explanation {
  source: 'gemini' | 'fallback'; model: string | null; risk_level: string; score: number;
  summary: string; limitations: string;
  findings: { statement: string; evidence_ids: string[]; policy_id: string }[];
  recommended_actions: { action: string; rationale: string; policy_id: string }[];
  tool_calls: { tool: string; args: Record<string, string> }[];
  verification: { ok: boolean; issues: string[] };
  privacy?: { pii_masked: boolean; replacements: number };
}
export interface Decision {
  id: string; customer_id: string; customer_name: string; as_of: string; action: string;
  decision: 'approved' | 'rejected'; note: string; officer: string; risk_level: string; score: number; created_at: string;
}
export interface Backtest {
  customers: number; defaulters: number; extra_lead_days: number | null;
  new_system: { caught: number; false_positives: number; precision: number; avg_lead_days_before_default: number };
  legacy_rule: { caught: number; false_positives: number; precision: number; avg_lead_days_before_default: number };
}
export interface Overview { nodes: { id: string; type: string; label: string }[]; edges: GraphEdge[]; }

export interface Levels { Low: number; Medium: number; High: number; }
export interface SeriesPoint extends Levels { month: string; exposure_high: number; legacy_flagged: number; }
export interface PortfolioSummary {
  as_of: string; customers: number; counts: Levels; exposure: Levels;
  heatmap: ({ industry: string } & Levels)[];
  new_high: { id: string; name: string; industry: string; score: number; loan_amount: number; signals: string[] }[];
  new_high_count: number; series: SeriesPoint[];
}
export interface WhatIfRequest {
  as_of: string; industry_shocks: Record<string, number>; failed_companies: string[];
  collateral_drop: number; collateral_kind: string | null;
}
export interface WhatIfResult {
  before: Levels; after: Levels; exposure_high_before: number; exposure_high_after: number; flipped_count: number;
  flipped: { id: string; name: string; industry: string; loan_amount: number; before: string; after: string;
             score_before: number; score_after: number; new_signals: string[]; via_companies: string[] }[];
  by_industry: { industry: string; before_high: number; after_high: number }[];
}
export interface CompanyDep { id: string; name: string; customers: number; }
export interface ChatReply { privacy?: { pii_masked: boolean; replacements: number }; answer: string; source: 'gemini' | 'fallback'; tool_calls: { tool: string; args: Record<string, unknown> }[]; cited: { id: string; name: string }[]; }
export interface ChatTurn { role: 'user' | 'model'; text: string; }

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);
  customers = (asOf: string) => this.http.get<CustomerRow[]>(`${API}/customers`, { params: { as_of: asOf } });
  context = (id: string, asOf: string) => this.http.get<Context>(`${API}/customers/${id}/context`, { params: { as_of: asOf } });
  timeline = (id: string) => this.http.get<TimelinePoint[]>(`${API}/customers/${id}/timeline`);
  explain = (id: string, asOf: string, cachedOnly = false) =>
    this.http.get<Explanation | null>(`${API}/customers/${id}/explain`, { params: { as_of: asOf, cached_only: cachedOnly } });
  decide = (id: string, body: object) => this.http.post<Decision>(`${API}/customers/${id}/decision`, body);
  decisions = (id: string) => this.http.get<Decision[]>(`${API}/decisions`, { params: { customer_id: id } });
  backtest = () => this.http.get<Backtest>(`${API}/backtest`);
  overview = () => this.http.get<Overview>(`${API}/graph/overview`);
  summary = (asOf: string) => this.http.get<PortfolioSummary>(`${API}/portfolio/summary`, { params: { as_of: asOf } });
  whatIf = (body: WhatIfRequest) => this.http.post<WhatIfResult>(`${API}/whatif`, body);
  companies = () => this.http.get<CompanyDep[]>(`${API}/companies`);
  chat = (question: string, asOf: string, history: ChatTurn[]) =>
    this.http.post<ChatReply>(`${API}/chat`, { question, as_of: asOf, history });
}
