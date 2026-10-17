import { Component, computed, inject, output, signal } from '@angular/core';
import { Api, Effect, SIGNAL_LABELS, Task, bn, lvlClass } from './api';
import { ACTION_LABELS } from './labels';

const COLUMNS: { id: Task['status']; label: string }[] = [
  { id: 'todo', label: 'To do' }, { id: 'doing', label: 'In progress' }, { id: 'done', label: 'Done' },
];
const monthFmt = new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
const DAY = 86_400_000;

/** Approved actions become tasks with an SLA. Completing one applies the mitigation and measures its effect with the same rules. */
@Component({
  selector: 'app-action-board',
  template: `
  <section class="cc board" aria-label="Action board">
    <div class="cc-head">
      <div>
        <h2>Action board <small>From alert to effect</small></h2>
        <p class="muted">Every approved proposal lands here with a deadline. Completing a task applies the mitigation to the portfolio and the
          rules re-score the borrower, so you see what the action actually bought. Drag a card or use its buttons.</p>
      </div>
    </div>

    <dl class="tiles">
      <div class="tile"><dt>Open</dt><dd>{{ count('todo') + count('doing') }}</dd><small>{{ count('doing') }} in progress</small></div>
      <div class="tile high"><dt>Overdue</dt><dd>{{ overdue() }}</dd><small>past their SLA</small></div>
      <div class="tile"><dt>Done</dt><dd>{{ count('done') }}</dd><small>mitigations applied</small></div>
      <div class="tile new"><dt>Risk points removed</dt><dd>{{ removed() }}</dd><small>score reduction from completed tasks</small></div>
    </dl>

    <div class="kanban">
      @for (col of columns; track col.id) {
        <div class="col" [class.over]="dragOver() === col.id" (dragover)="$event.preventDefault(); dragOver.set(col.id)"
             (dragleave)="dragOver.set(null)" (drop)="drop(col.id)" [attr.aria-label]="col.label">
          <h3>{{ col.label }} <small>{{ count(col.id) }}</small></h3>
          @for (t of byStatus(col.id); track t.id) {
            <article class="task" draggable="true" (dragstart)="dragging = t" (dragend)="dragOver.set(null)" [class.busy]="busyId() === t.id">
              <div class="task-top">
                <b>{{ label(t.action) }}</b>
                <span class="tag">{{ t.source === 'gemini' ? 'Gemini' : t.source === 'rules' ? 'Rules' : 'Manual' }}</span>
              </div>
              <button type="button" class="link" (click)="openCase.emit({ id: t.customer_id, month: t.as_of })">{{ t.customer_name }}</button>
              <small>{{ t.industry }}, {{ bn(t.loan_amount) }} bn VND, flagged {{ month(t.as_of) }}</small>
              @if (t.status !== 'done') {
                <small [class.late]="late(t)">{{ due(t) }}</small>
                <p class="exp-line">Expected: {{ effectText(t.expected) }}</p>
              } @else if (t.result; as r) {
                <div class="result">
                  <span class="badge {{ lvl(r.level_before) }}">{{ r.level_before }}</span> → <span class="badge {{ lvl(r.level_after) }}">{{ r.level_after }}</span>
                  <b class="pts">{{ r.score_before }} → {{ r.score_after }}</b>
                  <small>{{ r.effect }}</small>
                  @if (r.inflow_after < r.inflow_before) { <small>Contagion inflow {{ pct(r.inflow_before) }} → {{ pct(r.inflow_after) }}</small> }
                  @if (r.removed_signals.length) { <small>Cleared: {{ signals(r.removed_signals) }}</small> }
                </div>
              }
              @if (t.note) { <small>“{{ t.note }}”</small> }
              <div class="btns">
                @switch (t.status) {
                  @case ('todo') { <button type="button" class="btn ghost" (click)="move(t, 'doing')">Start</button><button type="button" class="btn ok" (click)="move(t, 'done')">Complete</button> }
                  @case ('doing') { <button type="button" class="btn ok" (click)="move(t, 'done')">Complete</button> }
                  @case ('done') { <button type="button" class="btn ghost" (click)="move(t, 'doing')">Reopen (undo)</button> }
                }
              </div>
            </article>
          } @empty { <p class="muted tiny">{{ col.id === 'todo' ? 'Approve a recommended action in Case review, or add one there.' : 'Nothing here.' }}</p> }
        </div>
      }
    </div>
  </section>`,
})
export class ActionBoard {
  private api = inject(Api);
  openCase = output<{ id: string; month: string }>();
  changed = output<string>();

  columns = COLUMNS;
  tasks = signal<Task[]>([]);
  dragOver = signal<Task['status'] | null>(null);
  busyId = signal<string | null>(null);
  dragging: Task | null = null;
  private now = Date.now();

  overdue = computed(() => this.tasks().filter((t) => this.late(t)).length);
  removed = computed(() => this.tasks().reduce((s, t) => s + (t.result ? Math.max(0, t.result.score_before - t.result.score_after) : 0), 0));

  constructor() { this.load(); }

  load() { this.api.tasks().subscribe((t) => this.tasks.set(t)); }

  byStatus = (s: Task['status']) => this.tasks().filter((t) => t.status === s);
  count = (s: Task['status']) => this.byStatus(s).length;

  drop(status: Task['status']) {
    this.dragOver.set(null);
    if (this.dragging && this.dragging.status !== status) this.move(this.dragging, status);
    this.dragging = null;
  }

  move(t: Task, status: Task['status']) {
    this.busyId.set(t.id);
    this.api.setTaskStatus(t.id, status).subscribe({
      next: (u) => {
        this.busyId.set(null);
        this.tasks.update((all) => all.map((x) => (x.id === u.id ? u : x)));
        if (u.status === 'done' && u.result) {
          this.changed.emit(`${u.customer_name}: ${this.label(u.action)} done. Score ${u.result.score_before} → ${u.result.score_after}, ${u.result.level_before} → ${u.result.level_after}.`);
        } else if (t.status === 'done') {
          this.changed.emit(`${u.customer_name}: ${this.label(u.action)} reopened, its effect is undone.`);
        }
      },
      error: () => { this.busyId.set(null); this.changed.emit('Could not update the task. Check the backend.'); },
    });
  }

  late = (t: Task) => t.status !== 'done' && new Date(t.due).getTime() < this.now;
  due(t: Task) {
    const days = Math.round((new Date(t.due).getTime() - this.now) / DAY);
    return days < 0 ? `Overdue by ${-days} ${-days === 1 ? 'day' : 'days'}` : days === 0 ? 'Due today' : `Due in ${days} ${days === 1 ? 'day' : 'days'} (SLA ${t.sla_days}d)`;
  }
  effectText(e: Effect) {
    if (e.score_after === e.score_before && e.inflow_after >= e.inflow_before) return 'no change to the score (the action is recorded)';
    const parts = [`score ${e.score_before} → ${e.score_after}`];
    if (e.level_after !== e.level_before) parts.push(`${e.level_before} → ${e.level_after}`);
    if (e.removed_signals.length) parts.push(`clears ${this.signals(e.removed_signals)}`);
    if (e.inflow_after < e.inflow_before) parts.push(`contagion ${this.pct(e.inflow_before)} → ${this.pct(e.inflow_after)}`);
    return parts.join(', ');
  }
  label = (a: string) => ACTION_LABELS[a] ?? a;
  signals = (codes: string[]) => codes.map((c) => SIGNAL_LABELS[c] ?? c).join(', ').toLowerCase();
  pct = (v: number) => (v * 100).toFixed(1);
  month = (m: string) => monthFmt.format(new Date(m + 'T00:00:00Z'));
  lvl = lvlClass;
  bn = bn;
}
