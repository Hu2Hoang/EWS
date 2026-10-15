import { Component, OnDestroy, computed, inject, output, signal } from '@angular/core';
import { Api, PortfolioSummary, SeriesPoint } from './api';

const monthFmt = new Intl.DateTimeFormat('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' });
const monthLong = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const bnFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const LATEST = '2026-09-01';

/** Portfolio-level view: risk mix over time (with a Play button), industry heatmap and fresh High-risk alerts. */
@Component({
  selector: 'app-command-center',
  template: `
  @if (data(); as d) {
    <section class="cc" aria-label="Portfolio command center">
      <div class="cc-head">
        <div>
          <h2>Portfolio command center <small>{{ long(d.as_of) }}</small></h2>
          <p class="muted">{{ num(d.customers) }} loans monitored. Every flag below comes from the rules engine and cites its evidence.</p>
        </div>
        <div class="play">
          <button type="button" class="btn run" (click)="toggle()">{{ playing() ? 'Pause' : 'Play timeline' }}</button>
          <label class="sr" for="cc-month">Assessment month</label>
          <input id="cc-month" type="range" min="0" [max]="months().length - 1" [value]="idx()"
                 [attr.aria-valuetext]="long(d.as_of)" (input)="scrub(+$any($event.target).value)" />
        </div>
      </div>

      <dl class="tiles">
        <div class="tile high"><dt>High risk</dt><dd>{{ d.counts.High }}</dd><small>{{ bn(d.exposure.High) }} bn VND exposed</small></div>
        <div class="tile mid"><dt>Medium risk</dt><dd>{{ d.counts.Medium }}</dd><small>{{ bn(d.exposure.Medium) }} bn VND</small></div>
        <div class="tile low"><dt>Low risk</dt><dd>{{ d.counts.Low }}</dd><small>{{ bn(d.exposure.Low) }} bn VND</small></div>
        <div class="tile new"><dt>New high this month</dt><dd>+{{ d.new_high_count }}</dd><small>moved up from Medium or Low</small></div>
      </dl>

      <div class="cc-grid">
        <div class="card pad-card">
          <h3>Loans flagged over time <small>Bars: Medium + High risk loans</small></h3>
          <svg viewBox="0 0 100 44" preserveAspectRatio="none" class="trend" role="img"
               [attr.aria-label]="'High-risk loans rose to ' + d.counts.High + ' while the legacy rule flagged ' + legacyNow()">
            @for (b of bars(); track b.month) {
              <rect [attr.x]="b.x" [attr.y]="b.y" [attr.width]="b.w" [attr.height]="b.h" class="bar" [class.on]="b.on" />
            }
            <polyline [attr.points]="legacyLine()" class="legacy" vector-effect="non-scaling-stroke" />
          </svg>
          <div class="axis"><span>{{ short(months()[0]) }}</span><span>{{ short(months()[months().length - 1]) }}</span></div>
          <ul class="marks">
            <li class="m-new"><i></i>Early Warning: loans at High risk now <b>{{ d.counts.High }}</b></li>
            <li class="m-old"><i></i>Legacy rule: loans ever 30+ days past due <b>{{ legacyNow() }}</b></li>
          </ul>
        </div>

        <div class="card pad-card">
          <h3>Industry heatmap <small>Click a row to open its High-risk loans</small></h3>
          <table class="heat">
            <thead><tr><th scope="col">Industry</th><th scope="col">High</th><th scope="col">Medium</th><th scope="col">Low</th></tr></thead>
            <tbody>
              @for (r of d.heatmap; track r.industry) {
                <tr>
                  <th scope="row"><button type="button" class="link" (click)="openIndustry.emit(r.industry)">{{ r.industry }}</button></th>
                  <td [style.background]="tint(r.High, r, 'high')">{{ r.High }}</td>
                  <td [style.background]="tint(r.Medium, r, 'mid')">{{ r.Medium }}</td>
                  <td class="plain">{{ r.Low }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>

        <div class="card pad-card wide">
          <h3>Newly high risk in {{ long(d.as_of) }} <small>Largest exposure first</small></h3>
          @for (n of d.new_high; track n.id) {
            <button type="button" class="alert" (click)="openCase.emit({ id: n.id, month: d.as_of })">
              <span class="dot high" aria-hidden="true"></span>
              <span class="nm">{{ n.name }}<small>{{ n.industry }}, {{ n.signals.length }} signals</small></span>
              <span class="amt">{{ bn(n.loan_amount) }} bn</span>
              <span class="go" aria-hidden="true">Open case</span>
            </button>
          } @empty { <p class="muted">No loan moved to High risk this month.</p> }
        </div>
      </div>
    </section>
  } @else {
    <p class="muted pad" role="status">{{ error() || 'Loading portfolio…' }}</p>
  }`,
  imports: [],
})
export class CommandCenter implements OnDestroy {
  private api = inject(Api);
  openCase = output<{ id: string; month: string }>();
  openIndustry = output<string>();

  data = signal<PortfolioSummary | null>(null);
  full = signal<SeriesPoint[]>([]);
  idx = signal(0);
  playing = signal(false);
  error = signal('');
  private timer?: ReturnType<typeof setInterval>;

  months = computed(() => this.full().map((s) => s.month));
  legacyNow = computed(() => this.full()[this.idx()]?.legacy_flagged ?? 0);
  private ymax = computed(() => Math.max(10, ...this.full().map((s) => s.Medium + s.High)) * 1.1);
  bars = computed(() => {
    const f = this.full(), n = f.length, top = this.ymax(), w = 100 / n;
    return f.map((s, i) => {
      const h = ((s.Medium + s.High) / top) * 40;
      return { month: s.month, x: i * w + w * 0.15, w: w * 0.7, y: 42 - h, h, on: i === this.idx() };
    });
  });
  legacyLine = computed(() => {
    const f = this.full(), n = f.length, top = this.ymax();
    return f.map((s, i) => `${(i + 0.5) * (100 / n)},${42 - (s.legacy_flagged / top) * 40}`).join(' ');
  });

  constructor() {
    this.api.summary(LATEST).subscribe({
      next: (d) => {
        this.full.set(d.series);
        // Open on the month where the most loans newly turned High, so the first screen shows action rather than a quiet month.
        const jump = d.series.map((s, i) => (i ? s.High - d.series[i - 1].High : 0));
        const best = jump.indexOf(Math.max(...jump));
        this.idx.set(best);
        this.api.summary(d.series[best].month).subscribe((m) => this.data.set(m));
      },
      error: () => this.error.set('Cannot reach the backend. Start it and reload.'),
    });
  }

  ngOnDestroy() { this.stop(); }

  scrub(i: number) {
    this.stop();
    this.go(i);
  }

  toggle() {
    if (this.playing()) return this.stop();
    if (this.idx() >= this.months().length - 1) this.go(0);
    this.playing.set(true);
    this.timer = setInterval(() => {
      if (this.idx() >= this.months().length - 1) return this.stop();
      this.go(this.idx() + 1);
    }, 1100);
  }

  private stop() { clearInterval(this.timer); this.playing.set(false); }

  private go(i: number) {
    this.idx.set(i);
    this.api.summary(this.months()[i]).subscribe((d) => this.idx() === i && this.data.set(d));
  }

  tint(n: number, r: { High: number; Medium: number; Low: number }, kind: 'high' | 'mid') {
    const share = n / Math.max(1, r.High + r.Medium + r.Low);
    const a = Math.min(0.85, share * (kind === 'high' ? 14 : 6));
    return n ? `rgb(${kind === 'high' ? '163 38 47' : '176 122 31'} / ${(a * 0.3).toFixed(2)})` : 'transparent';
  }
  num = (v: number) => bnFmt.format(v);
  bn = (v: number) => bnFmt.format(v / 1000);
  short = (m?: string) => (m ? monthFmt.format(new Date(m + 'T00:00:00Z')) : '');
  long = (m?: string) => (m ? monthLong.format(new Date(m + 'T00:00:00Z')) : '');
}
