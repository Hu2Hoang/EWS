import { Component, computed, effect, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, CompanyDep, Levels, WhatIfResult } from './api';

const bnFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const AS_OF = '2026-09-01';
const INDUSTRIES = ['Seafood', 'Real Estate', 'Textiles', 'Logistics', 'Agriculture', 'Retail'];
const KINDS = ['All', 'Real Estate', 'Machinery', 'Inventory'];

interface Preset { label: string; industry: string; drop: number; company: string; col: number; kind: string; }

/** What-if lab: apply a shock to a copy of the portfolio and see which loans cross a risk threshold. Nothing is written. */
@Component({
  selector: 'app-stress-test',
  imports: [FormsModule],
  template: `
  <section class="stress" aria-label="Stress test">
    <div class="cc-head">
      <div>
        <h2>Stress test <small>What if…?</small></h2>
        <p class="muted">Apply a shock to a copy of the portfolio. The same rules re-score every loan, so each change is explained by the signals it triggers. Nothing is saved.</p>
      </div>
    </div>

    <div class="presets" role="group" aria-label="Scenario presets">
      @for (p of presets; track p.label) {
        <button type="button" class="preset" (click)="apply(p)">{{ p.label }}</button>
      }
      <button type="button" class="preset ghost" (click)="reset()">Reset</button>
    </div>

    <div class="stress-grid">
      <form class="card pad-card ctrl" (submit)="$event.preventDefault()" aria-label="Scenario controls">
        <h3>Industry downturn</h3>
        <label for="ind">Industry</label>
        <select id="ind" name="industry" [ngModel]="industry()" (ngModelChange)="industry.set($event)">
          <option value="">None</option>
          @for (i of industries; track i) { <option [value]="i">{{ i }}</option> }
        </select>
        <label for="drop">Revenue drop <b>{{ drop() }}%</b></label>
        <input id="drop" name="drop" type="range" min="0" max="50" step="5" [disabled]="!industry()" [ngModel]="drop()" (ngModelChange)="drop.set(+$event)" />

        <h3>Related company failure</h3>
        <label for="co">Company that becomes past due</label>
        <select id="co" name="company" [ngModel]="company()" (ngModelChange)="company.set($event)">
          <option value="">None</option>
          @for (c of companies(); track c.id) { <option [value]="c.id">{{ c.name }} ({{ c.customers }} loans)</option> }
        </select>

        <h3>Collateral shock</h3>
        <label for="ck">Collateral type</label>
        <select id="ck" name="kind" [ngModel]="kind()" (ngModelChange)="kind.set($event)">
          @for (k of kinds; track k) { <option [value]="k">{{ k }}</option> }
        </select>
        <label for="cd">Value drop <b>{{ col() }}%</b></label>
        <input id="cd" name="col" type="range" min="0" max="30" step="5" [ngModel]="col()" (ngModelChange)="col.set(+$event)" />
      </form>

      <div class="results" aria-live="polite">
        @if (res(); as r) {
          <div class="card pad-card">
            <h3>Portfolio impact <small>{{ running() ? 'Recalculating…' : '' }}</small></h3>
            <div class="impact">
              @for (l of levelRows(r); track l.name) {
                <div class="irow">
                  <span class="lname">{{ l.name }}</span>
                  <div class="bars">
                    <i class="b {{ l.cls }} faint" [style.width.%]="l.before"></i>
                    <i class="b {{ l.cls }}" [style.width.%]="l.after"></i>
                  </div>
                  <span class="nums">{{ l.nb }} to <b>{{ l.na }}</b></span>
                </div>
              }
            </div>
            <div class="exp">
              <div><small>High-risk exposure before</small><b>{{ bn(r.exposure_high_before) }} bn VND</b></div>
              <div><small>High-risk exposure after</small><b class="neg">{{ bn(r.exposure_high_after) }} bn VND</b></div>
              <div><small>Loans moving up a level</small><b>{{ r.flipped_count }}</b></div>
            </div>
          </div>

          <div class="card pad-card">
            <h3>Most exposed loans <small>Click to open the case</small></h3>
            @for (f of r.flipped.slice(0, 10); track f.id) {
              <button type="button" class="alert" (click)="openCase.emit(f.id)">
                <span class="dot {{ f.after === 'High' ? 'high' : 'mid' }}" aria-hidden="true"></span>
                <span class="nm">{{ f.name }}<small>{{ f.industry }}, {{ f.before }} to {{ f.after }}{{ f.via_companies.length ? ', via related company' : '' }}</small></span>
                <span class="amt">{{ bn(f.loan_amount) }} bn</span>
                <span class="go" aria-hidden="true">Open case</span>
              </button>
            } @empty { <p class="muted">No loan changes risk level under this scenario.</p> }
          </div>
        } @else {
          <p class="muted pad" role="status">{{ error() || 'Pick a scenario to see the impact.' }}</p>
        }
      </div>
    </div>
  </section>`,
})
export class StressTest {
  private api = inject(Api);
  openCase = output<string>();

  industries = INDUSTRIES;
  kinds = KINDS;
  industry = signal('Real Estate');
  drop = signal(25);
  company = signal('');
  col = signal(0);
  kind = signal('All');
  companies = signal<CompanyDep[]>([]);
  res = signal<WhatIfResult | null>(null);
  running = signal(false);
  error = signal('');

  presets: Preset[] = [
    { label: 'Property market slump', industry: 'Real Estate', drop: 25, company: '', col: 0, kind: 'All' },
    { label: 'Seafood export crash', industry: 'Seafood', drop: 35, company: '', col: 0, kind: 'All' },
    { label: 'Collateral values fall 15%', industry: '', drop: 0, company: '', col: 15, kind: 'All' },
    { label: 'Largest related company defaults', industry: '', drop: 0, company: '@top', col: 0, kind: 'All' },
    { label: 'Combined shock', industry: 'Retail', drop: 20, company: '@top', col: 10, kind: 'All' },
  ];

  private total = computed(() => {
    const r = this.res();
    return r ? Math.max(1, r.before.Low + r.before.Medium + r.before.High) : 1;
  });

  constructor() {
    this.api.companies().subscribe((c) => this.companies.set(c));
    // Debounced re-run whenever a control changes.
    effect((onCleanup) => {
      const body = {
        as_of: AS_OF,
        industry_shocks: this.industry() && this.drop() ? { [this.industry()]: this.drop() } : {},
        failed_companies: this.company() ? [this.company()] : [],
        collateral_drop: this.col(), collateral_kind: this.kind() === 'All' ? null : this.kind(),
      };
      const t = setTimeout(() => {
        this.running.set(true);
        this.api.whatIf(body).subscribe({
          next: (r) => { this.res.set(r); this.running.set(false); this.error.set(''); },
          error: () => { this.running.set(false); this.error.set('The simulation failed. Check that the backend is running.'); },
        });
      }, 250);
      onCleanup(() => clearTimeout(t));
    });
  }

  apply(p: Preset) {
    this.industry.set(p.industry);
    this.drop.set(p.drop);
    this.company.set(p.company === '@top' ? this.companies()[0]?.id ?? '' : p.company);
    this.col.set(p.col);
    this.kind.set(p.kind);
  }

  reset() { this.apply({ label: '', industry: '', drop: 0, company: '', col: 0, kind: 'All' }); }

  levelRows(r: WhatIfResult) {
    const t = this.total();
    const row = (name: keyof Levels, cls: string) => ({
      name, cls, before: (r.before[name] / t) * 100, after: (r.after[name] / t) * 100,
      nb: r.before[name], na: r.after[name],
    });
    return [row('High', 'high'), row('Medium', 'mid'), row('Low', 'low')];
  }

  bn = (v: number) => bnFmt.format(v / 1000);
}
