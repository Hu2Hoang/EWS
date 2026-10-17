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
  events: { id: string; type: string; date: string; description: string; source?: string }[];
  policies: { id: string; title: string; text: string }[];
  mitigations?: { id: string; task_id: string | null; action: string; as_of: string; effect: string }[];
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
  task?: Task;
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

export interface ShockNode { id: string; type: string; label: string; state: 'seed' | 'default' | 'hit' | 'path'; hop: number | null; round: number | null; delta: number | null; own: number; }
export interface Affected {
  id: string; name: string; industry: string; loan_amount: number; hop: number | null; round: number | null; defaulted: boolean;
  inflow_before: number; inflow_after: number; delta: number; total_before: number; total_after: number;
  watch_before: boolean; watch_after: boolean; level_before: string; level_after: string;
}
export interface ShockResult {
  as_of: string; alpha: number; hops: number; threshold: number;
  seeds: { id: string; label: string; type: string; own_before: number; loan_amount: number | null }[];
  rounds: { round: number; defaults: { id: string; name: string }[] }[];
  summary: { affected: number; cascade_defaults: number; new_watch: number; level_ups: number; exposure_default: number; exposure_affected: number };
  affected: Affected[];
  graph: { nodes: ShockNode[]; edges: GraphEdge[] };
}
export interface MatrixCell { industry: string; value: number; count: number; }
export interface ContagionMatrix {
  as_of: string; industries: string[]; max_cell: number; totals: MatrixCell[];
  rows: { id: string; label: string; type: string; own: number; reach: number; total: number; cells: MatrixCell[] }[];
  channels: { relation: string; total: number; cells: MatrixCell[] }[];
}
export interface Candidate { id: string; label: string; type: string; own: number; customers: number; exposure: number; }
export interface Receiver { id: string; name: string; industry: string; loan_amount: number; contribution: number; relation: string; hops: number; inflow: number; own: number; }

export interface Impact {
  as_of: string;
  summary: { level_up: number; level_down: number; score_changed: number; newly_watch: number; watch_cleared: number; contagion_changed: number; exposure_high_delta: number };
  level_changes: { id: string; name: string; industry: string; loan_amount: number; level_before: string; level_after: string;
                   score_before: number; score_after: number; added_signals: string[]; removed_signals: string[] }[];
  contagion_changes: { id: string; name: string; inflow_before: number; inflow_after: number; delta: number; watch_before: boolean; watch_after: boolean }[];
}
export interface EventDraft {
  id: string; as_of: string; target_type: 'Company' | 'Customer' | 'Industry'; target_id: string; event_type: string;
  severity: 'low' | 'medium' | 'high'; description: string; source: string; text: string; created_at: string;
}
export interface NewsEvent {
  entity: string; entity_type: string; event_type: string; severity: 'low' | 'medium' | 'high'; summary: string; quote: string;
  linked: { type: string; id: string; name: string; match: string } | null; draft: EventDraft | null; preview: Impact | null;
}
export interface NewsAnalysis { as_of: string; source: 'gemini' | 'keywords'; model: string | null; events: NewsEvent[]; privacy: { pii_masked: boolean; replacements: number }; }
export interface Effect {
  level_before: string; level_after: string; score_before: number; score_after: number; inflow_before: number; inflow_after: number;
  removed_signals: string[]; added_signals: string[]; neighbours_relieved: number;
}
export interface Task {
  id: string; customer_id: string; customer_name: string; industry: string; loan_amount: number; action: string; as_of: string;
  status: 'todo' | 'doing' | 'done'; officer: string; note: string; source: string; created_at: string; due: string; sla_days: number;
  effect: string; expected: Effect; result: (Effect & { effect: string; summary: Impact['summary'] }) | null;
}

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

  shock = (body: { as_of: string; seeds: string[]; alpha: number; hops: number; threshold: number }) =>
    this.http.post<ShockResult>(`${API}/contagion/shock`, body);
  matrix = (asOf: string) => this.http.get<ContagionMatrix>(`${API}/contagion/matrix`, { params: { as_of: asOf } });
  candidates = (asOf: string) => this.http.get<Candidate[]>(`${API}/contagion/candidates`, { params: { as_of: asOf } });
  receivers = (id: string, asOf: string) => this.http.get<Receiver[]>(`${API}/contagion/source/${encodeURIComponent(id)}`, { params: { as_of: asOf } });

  tasks = (customerId?: string) => this.http.get<Task[]>(`${API}/tasks`, { params: customerId ? { customer_id: customerId } : {} });
  createTask = (body: { customer_id: string; action: string; as_of: string; note?: string }) => this.http.post<Task>(`${API}/tasks`, body);
  setTaskStatus = (id: string, status: Task['status']) => this.http.post<Task>(`${API}/tasks/${id}/status`, { status });

  analyzeNews = (text: string, asOf: string) => this.http.post<NewsAnalysis>(`${API}/news/analyze`, { text, as_of: asOf });
  newsSamples = (asOf: string) => this.http.get<{ label: string; text: string }[]>(`${API}/news/samples`, { params: { as_of: asOf } });
  events = () => this.http.get<EventDraft[]>(`${API}/events`);
  applyEvent = (d: EventDraft) => this.http.post<{ overlay: EventDraft; impact: Impact }>(`${API}/events`, d);
  undoEvent = (id: string) => this.http.delete<{ ok: boolean }>(`${API}/events/${id}`);
  resetLive = () => this.http.post<{ ok: boolean }>(`${API}/live/reset`, {});
}

export const SIGNAL_LABELS: Record<string, string> = {
  revenue_drop: 'Revenue drop', negative_cashflow: 'Negative cash flow', related_overdue: 'Related company past due',
  collateral_drop: 'Collateral decline', dpd: 'Days past due', industry_downturn: 'Industry downturn', low_deposit_coverage: 'Low deposit coverage',
};
export const EVENT_LABELS: Record<string, string> = {
  payment_default: 'Missed payment', bankruptcy: 'Insolvency', tax_enforcement: 'Tax enforcement', legal_action: 'Legal action',
  revenue_loss: 'Revenue loss', collateral_impairment: 'Collateral impairment', industry_downturn: 'Industry downturn',
};
export const bn = (v: number) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(v / 1000);
export const lvlClass = (l: string) => (l === 'High' ? 'high' : l === 'Medium' ? 'mid' : 'low');
