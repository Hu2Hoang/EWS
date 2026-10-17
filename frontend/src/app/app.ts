import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Backtest, Context, CustomerRow, Decision, Explanation, Overview, Signal, Task, TimelinePoint } from './api';
import { ActionBoard } from './action-board';
import { Assistant } from './assistant';
import { CommandCenter } from './command-center';
import { ContagionLab } from './contagion-lab';
import { GraphView } from './graph-view';
import { ACTION_LABELS } from './labels';
import { LiveEvents } from './live-events';
import { StressTest } from './stress-test';

export { ACTION_LABELS };
export const LEGEND = [
  { type: 'Customer', label: 'Customer' }, { type: 'Loan', label: 'Loan' }, { type: 'Collateral', label: 'Collateral' },
  { type: 'Industry', label: 'Industry' }, { type: 'Company', label: 'Related Co.' },
  { type: 'Guarantor', label: 'Guarantor' }, { type: 'RiskEvent', label: 'Risk Event' },
];
const DEFAULT_CUSTOMER = 'C_A';
const VIEWS = ['overview', 'case', 'contagion', 'events', 'actions', 'stress'] as const;
const SHOCKABLE = ['Customer', 'Company', 'Guarantor'];
type View = (typeof VIEWS)[number];
const LIST_CAP = 150;
const RING = 2 * Math.PI * 26;
const monthFmt = new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
const numFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const bnFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const dateTimeFmt = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short' });

@Component({
  selector: 'app-root',
  imports: [FormsModule, GraphView, CommandCenter, StressTest, Assistant, ContagionLab, LiveEvents, ActionBoard],
  templateUrl: './app.html',
})
export class App {
  private api = inject(Api);

  customers = signal<CustomerRow[]>([]);
  selectedId = signal(DEFAULT_CUSTOMER);
  timeline = signal<TimelinePoint[]>([]);
  idx = signal(0);
  ctx = signal<Context | null>(null);
  overview = signal<Overview | null>(null);
  backtest = signal<Backtest | null>(null);
  explanation = signal<Explanation | null>(null);
  explaining = signal(false);
  decisions = signal<Decision[]>([]);
  focusId = signal<string | null>(null);
  note = signal('');
  search = signal('');
  levelFilter = signal<'All' | 'High' | 'Medium' | 'Low'>('All');
  view = signal<View>('overview');
  toast = signal('');
  error = signal('');
  caseTasks = signal<Task[]>([]);
  newAction = signal('request_additional_collateral');
  labStart = signal<{ month: string; seeds: string[] } | null>(null);
  private toastTimer?: ReturnType<typeof setTimeout>;

  actionLabels = ACTION_LABELS;
  actionIds = Object.keys(ACTION_LABELS).filter((a) => a !== 'no_action');
  legend = LEGEND;
  levels = ['All', 'High', 'Medium', 'Low'] as const;
  tabs: { id: View; label: string }[] = [
    { id: 'overview', label: 'Command center' }, { id: 'case', label: 'Case review' }, { id: 'contagion', label: 'Contagion lab' },
    { id: 'events', label: 'News to graph' }, { id: 'actions', label: 'Action board' }, { id: 'stress', label: 'Stress test' },
  ];

  asOf = computed(() => this.timeline()[this.idx()]?.month ?? '');
  months = computed(() => this.timeline().map((p) => p.month));
  /** The graph node in focus, when it is something that can be made to default in the contagion lab. */
  focusNode = computed(() => {
    const id = this.focusId(), n = this.ctx()?.graph.nodes.find((x) => x.id === id);
    return n && SHOCKABLE.includes(n.type) ? n : null;
  });
  matching = computed(() => {
    const q = this.search().trim().toLowerCase(), f = this.levelFilter();
    return this.customers().filter((c) =>
      (f === 'All' || c.level === f) && (!q || `${c.name} ${c.industry} ${c.segment}`.toLowerCase().includes(q)));
  });
  visibleCustomers = computed(() => this.matching().slice(0, LIST_CAP));
  hiddenCount = computed(() => Math.max(0, this.matching().length - LIST_CAP));
  ringDash = computed(() => `${((this.ctx()?.assessment.score ?? 0) / 100) * RING} ${RING}`);
  areaPoints = computed(() => `0,50 ${this.linePoints()} 100,50`);
  linePoints = computed(() => {
    const t = this.timeline();
    return t.map((p, i) => `${(i / Math.max(1, t.length - 1)) * 100},${48 - p.score * 0.44}`).join(' ');
  });
  markers = computed(() => {
    const t = this.timeline(), c = this.ctx()?.customer;
    const x = (m: string | null | undefined) => {
      const i = t.findIndex((p) => p.month === m);
      return i < 0 ? null : (i / Math.max(1, t.length - 1)) * 100;
    };
    const warn = t.find((p) => p.level === 'High')?.month;
    return [
      { label: 'AI Early Warning', cls: 'm-new', x: x(warn), month: warn },
      { label: 'Legacy Rule Flag', cls: 'm-old', x: x(c?.legacy_flag_date), month: c?.legacy_flag_date },
      { label: 'Default', cls: 'm-def', x: x(c?.default_date), month: c?.default_date },
    ].filter((m) => m.x !== null);
  });
  cursorX = computed(() => (this.idx() / Math.max(1, this.timeline().length - 1)) * 100);
  cursorY = computed(() => 48 - (this.timeline()[this.idx()]?.score ?? 0) * 0.44);

  constructor() {
    this.api.overview().subscribe((o) => this.overview.set(o));
    this.api.backtest().subscribe((b) => this.backtest.set(b));
    const q = new URLSearchParams(location.search);
    const v = q.get('v');
    this.view.set((VIEWS as readonly string[]).includes(v ?? '') ? (v as View) : q.get('c') ? 'case' : 'overview');
    const lv = q.get('l');
    if (lv && (this.levels as readonly string[]).includes(lv)) this.levelFilter.set(lv as 'All' | 'High' | 'Medium' | 'Low');
    this.search.set(q.get('q') ?? '');
    // Keep the URL in sync so any view (customer, month, filters) can be shared and reloaded.
    effect(() => {
      const p = new URLSearchParams();
      p.set('v', this.view());
      p.set('c', this.selectedId());
      // Until the timeline loads, keep the month from the address bar so an early reload does not drop it.
      const month = this.asOf() ? this.asOf().slice(0, 7) : q.get('m');
      if (month) p.set('m', month);
      if (this.levelFilter() !== 'All') p.set('l', this.levelFilter());
      if (this.search()) p.set('q', this.search());
      history.replaceState(null, '', `?${p}`);
    });
    this.api.customers('2026-09-01').subscribe({
      next: (rows) => { this.customers.set(rows); this.select(q.get('c') ?? DEFAULT_CUSTOMER, q.get('m')); },
      error: () => this.error.set('Cannot reach the backend at http://localhost:8000. Start it and reload.'),
    });
  }

  select(id: string, month: string | null = null) {
    this.selectedId.set(id);
    this.explanation.set(null);
    this.api.timeline(id).subscribe({
      next: (t) => {
        this.timeline.set(t);
        const i = month ? t.findIndex((p) => p.month.startsWith(month)) : -1;
        this.idx.set(i >= 0 ? i : t.length - 1);
        this.refresh();
      },
      // An id from an old link (for example after the data was reseeded) must not leave the page loading forever.
      error: () => { if (id !== DEFAULT_CUSTOMER) this.select(DEFAULT_CUSTOMER); else this.error.set('Cannot load the case. Check that the backend is running.'); },
    });
    this.api.decisions(id).subscribe((d) => this.decisions.set(d));
    this.loadTasks();
  }

  private loadTasks() {
    const id = this.selectedId();
    this.api.tasks(id).subscribe((t) => this.selectedId() === id && this.caseTasks.set(t));
  }

  /** Opens the contagion lab with this node defaulting at the given month. */
  simulate(id: string, month: string | null = null) {
    this.labStart.set({ month: month ?? (this.asOf() || '2026-09-01'), seeds: [id] });
    this.view.set('contagion');
    window.scrollTo({ top: 0 });
  }

  /** The live portfolio changed (news event applied, task completed): say so and reload the open case. */
  onChanged(msg: string) {
    this.notify(msg);
    const m = this.asOf();
    if (m) this.select(this.selectedId(), m.slice(0, 7));
  }

  addTask() {
    this.api.createTask({ customer_id: this.selectedId(), action: this.newAction(), as_of: this.asOf(), note: this.note() }).subscribe({
      next: (t) => { this.note.set(''); this.notify(`Task added to the action board: ${ACTION_LABELS[t.action]}.`); this.loadTasks(); },
      error: () => this.notify('Could not create the task. Check the backend.'),
    });
  }

  setTask(t: Task, status: Task['status']) {
    this.api.setTaskStatus(t.id, status).subscribe((u) => {
      const r = u.result;
      this.onChanged(r ? `${ACTION_LABELS[u.action]} done. Score ${r.score_before} → ${r.score_after}, ${r.level_before} → ${r.level_after}.`
                       : `${ACTION_LABELS[u.action]} reopened, its effect is undone.`);
    });
  }

  private notify(msg: string) {
    clearTimeout(this.toastTimer);
    this.toast.set(msg);
    this.toastTimer = setTimeout(() => this.toast.set(''), 5000);
  }

  /** Opens a case from any view (alert list, stress test, assistant citation). */
  openCase(id: string, month: string | null = null) {
    this.view.set('case');
    this.select(id, month ? month.slice(0, 7) : null);
    window.scrollTo({ top: 0 });
  }

  openIndustry(industry: string) {
    this.search.set(industry);
    this.levelFilter.set('High');
    this.view.set('case');
    const first = this.customers().find((c) => c.industry === industry && c.level === 'High');
    if (first) this.select(first.id);
  }

  scrub(i: number) {
    this.idx.set(i);
    this.refresh();
  }

  private refresh() {
    const id = this.selectedId(), m = this.asOf();
    if (!m) return;
    this.explanation.set(null);
    this.api.context(id, m).subscribe((c) => this.ctx.set(c));
    this.api.customers(m).subscribe((c) => this.customers.set(c));
    this.api.explain(id, m, true).subscribe((e) => e && this.selectedId() === id && this.asOf() === m && this.explanation.set(e));
  }

  runExplain() {
    this.explaining.set(true);
    this.api.explain(this.selectedId(), this.asOf()).subscribe({
      next: (e) => { this.explanation.set(e); this.explaining.set(false); },
      error: () => { this.error.set('The agent request failed. Check the backend log and try again.'); this.explaining.set(false); },
    });
  }

  decide(action: string, decision: 'approved' | 'rejected') {
    this.api.decide(this.selectedId(), {
      as_of: this.asOf(), action, decision, note: this.note(), ai_source: this.explanation()?.source ?? null,
    }).subscribe((d) => {
      this.note.set('');
      this.notify(`${decision === 'approved' ? 'Approved' : 'Rejected'}: ${ACTION_LABELS[action] ?? action}. Saved to the audit log`
                  + (d.task ? ' and added to the action board.' : '.'));
      this.api.decisions(this.selectedId()).subscribe((d) => this.decisions.set(d));
      this.loadTasks();
    });
  }

  month(m: string | null | undefined) { return m ? monthFmt.format(new Date(m + 'T00:00:00Z')) : ''; }
  when(iso: string) { return dateTimeFmt.format(new Date(iso)); }
  loan(v: number) { return `${bnFmt.format(v / 1000)}\u00A0bn\u00A0VND`; }
  lvl(l: string) { return l === 'High' ? 'high' : l === 'Medium' ? 'mid' : 'low'; }
  argText(a: Record<string, string>) { return Object.values(a).join(', '); }

  /** Human-readable supporting figures for a rule signal. */
  describe(s: Signal): string {
    const d = s.detail as Record<string, any>;
    switch (s.code) {
      case 'revenue_drop': return `3-month average ${numFmt.format(d['recent_avg'])} vs. ${numFmt.format(d['baseline_avg'])} baseline (VND mn)`;
      case 'negative_cashflow': return `Last two periods: ${d['last_two'].map((v: number) => numFmt.format(v)).join(' and ')} (VND mn)`;
      case 'related_overdue': return d['companies'].map((c: any) => `${c.company}: ${c.dpd} days past due`).join(', ');
      case 'collateral_drop': return `Latest ${numFmt.format(d['latest'])} vs. peak ${numFmt.format(d['peak'])} (VND mn)`;
      case 'dpd': return `${d['dpd']} days past due on the loan`;
      case 'industry_downturn': return `Industry growth ${d['growth_pct'].map((g: number) => g.toFixed(1) + '%').join(', ')} (last 2 months)`;
      default: return '';
    }
  }
}
