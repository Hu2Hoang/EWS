import { Component, OnDestroy, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { Api, EVENT_LABELS, EventDraft, Impact, NewsAnalysis, NewsEvent, SIGNAL_LABELS, bn, lvlClass } from './api';

const LATEST = '2026-09-01';
const monthLong = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const dateTimeFmt = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short' });

/** News to graph: Gemini extracts risk events from a headline, links them to graph nodes and previews the impact.
 *  Nothing changes until the officer applies an event; it can be undone from the event log. */
@Component({
  selector: 'app-live-events',
  imports: [FormsModule],
  template: `
  <section class="cc live" aria-label="Live events">
    <div class="cc-head">
      <div>
        <h2>News to graph <small>{{ long(month()) }}</small></h2>
        <p class="muted">Paste a headline or an article, in English or Vietnamese. Gemini extracts the credit-risk events, links each one to the
          knowledge graph and shows what it would change, before anything changes. Names are masked before the text reaches Gemini.</p>
      </div>
      <div class="play">
        <button type="button" class="btn run" (click)="feeding() ? stopFeed() : playFeed()">{{ feeding() ? 'Stop feed' : 'Play news feed' }}</button>
      </div>
    </div>

    <div class="stress-grid">
      <form class="ctrl" (submit)="$event.preventDefault(); analyze()" aria-label="News input">
        <h3>Sample headlines</h3>
        <div class="samples">
          @for (s of samples(); track s.label) { <button type="button" class="preset" (click)="text.set(s.text); analyze()">{{ s.label }}</button> }
        </div>
        <label for="news">Headline or article</label>
        <textarea id="news" name="news" rows="7" placeholder="e.g. Tax authority enforces collection against …" [ngModel]="text()" (ngModelChange)="text.set($event)"></textarea>
        <button type="submit" class="btn run" [disabled]="busy() || !text().trim()">{{ busy() ? 'Gemini is reading…' : 'Analyse with Gemini' }}</button>
        <p class="muted tiny">Effects start in {{ long(month()) }}: a company event makes it past due, a borrower event hits its cash flow or
          payments, an industry event marks a downturn. The rules and the contagion engine then re-score everyone.</p>
      </form>

      <div class="results" aria-live="polite">
        @if (result(); as r) {
          <div class="src">{{ r.source === 'gemini' ? 'Extracted by ' + r.model : 'Gemini unavailable: keyword extractor' }}@if (r.privacy.pii_masked) {, {{ r.privacy.replacements }} {{ r.privacy.replacements === 1 ? 'name' : 'names' }} masked before the LLM }</div>
          @for (e of r.events; track $index) {
            <article class="card pad-card news-ev" [class.done]="applied().has(e.draft?.id ?? '')">
              <div class="ev-top">
                <span class="badge {{ e.severity === 'high' ? 'high' : e.severity === 'medium' ? 'mid' : 'low' }}">{{ e.severity }}</span>
                <b>{{ eventLabel(e.event_type) }}</b>
                <span class="tag">{{ e.entity_type }}</span>
              </div>
              <p class="summary">{{ e.summary }}</p>
              @if (e.quote) { <p class="quote">“{{ e.quote }}”</p> }
              @if (e.linked; as l) {
                <p class="linked">Linked to <b>{{ l.name }}</b> <span class="tag" translate="no">{{ l.id }}</span> <small>{{ l.match }} match</small></p>
                @if (e.preview; as p) {
                  <ul class="impact-list">
                    <li><b>{{ p.summary.level_up }}</b> borrowers move up a risk level</li>
                    <li><b>{{ p.summary.score_changed }}</b> scores change, <b>{{ p.summary.newly_watch }}</b> newly on contagion watch</li>
                    @if (p.summary.exposure_high_delta) { <li><b>+{{ bn(p.summary.exposure_high_delta) }} bn VND</b> becomes High-risk exposure</li> }
                  </ul>
                  @for (c of p.level_changes.slice(0, 4); track c.id) {
                    <button type="button" class="alert" (click)="openCase.emit({ id: c.id, month: month() })">
                      <span class="dot {{ lvl(c.level_after) }}" aria-hidden="true"></span>
                      <span class="nm">{{ c.name }}<small>{{ c.level_before === c.level_after ? 'Still ' + c.level_after : c.level_before + ' to ' + c.level_after }}{{ c.added_signals.length ? ', ' + signals(c.added_signals) : '' }}</small></span>
                      <span class="amt">{{ c.score_before }} → {{ c.score_after }}</span>
                    </button>
                  }
                }
                <div class="btns">
                  @if (applied().has(e.draft!.id)) {
                    <span class="ok-note">Applied to the graph</span>
                    @if (e.linked.type !== 'Industry') { <button type="button" class="btn ghost" (click)="openContagion.emit({ id: e.linked.id, month: month() })">See the contagion</button> }
                  } @else {
                    <button type="button" class="btn ok" [disabled]="busy()" (click)="apply(e)">Apply to graph</button>
                    <button type="button" class="btn ghost" (click)="dismiss(e)">Dismiss</button>
                  }
                </div>
              } @else {
                <p class="muted">“{{ e.entity }}” is not in the portfolio, so nothing to apply.</p>
              }
            </article>
          } @empty { <p class="muted">No credit-risk event found in this text.</p> }
        } @else {
          <p class="muted pad">{{ error() || 'Pick a sample headline or paste your own.' }}</p>
        }

        <div class="card pad-card">
          <h3>Event log <small>Confirmed events, newest first</small></h3>
          @for (ev of log(); track ev.id) {
            <div class="log">
              <b class="approved">{{ eventLabel(ev.event_type) }}</b> {{ ev.description }}
              <small>{{ ev.target_type }} {{ ev.target_id }}, {{ ev.severity }} severity, effective {{ long(ev.as_of) }}, {{ when(ev.created_at) }}, via {{ ev.source }}</small>
              <button type="button" class="link" (click)="undo(ev)">Undo</button>
            </div>
          } @empty { <p class="muted">No event applied yet.</p> }
          <button type="button" class="btn ghost full" (click)="reset()">Reset demo (remove all events and tasks)</button>
        </div>
      </div>
    </div>
  </section>`,
})
export class LiveEvents implements OnDestroy {
  private api = inject(Api);
  asOf = input(LATEST);
  openCase = output<{ id: string; month: string }>();
  openContagion = output<{ id: string; month: string }>();
  changed = output<string>();

  text = signal('');
  busy = signal(false);
  error = signal('');
  result = signal<NewsAnalysis | null>(null);
  samples = signal<{ label: string; text: string }[]>([]);
  log = signal<EventDraft[]>([]);
  applied = signal(new Set<string>());
  feeding = signal(false);
  month = () => this.asOf() || LATEST;

  constructor() {
    this.loadLog();
    queueMicrotask(() => this.api.newsSamples(this.month()).subscribe((s) => this.samples.set(s)));
  }

  ngOnDestroy() { this.feeding.set(false); }

  analyze() {
    const t = this.text().trim();
    if (!t || this.busy()) return Promise.resolve(null);
    this.busy.set(true);
    this.error.set('');
    return firstValueFrom(this.api.analyzeNews(t, this.month())).then(
      (r) => { this.result.set(r); this.busy.set(false); return r; },
      () => { this.busy.set(false); this.error.set('The analysis failed. Check that the backend is running.'); return null; },
    );
  }

  apply(e: NewsEvent) {
    if (!e.draft) return Promise.resolve();
    this.busy.set(true);
    return firstValueFrom(this.api.applyEvent(e.draft)).then(
      (r) => {
        this.busy.set(false);
        this.applied.update((s) => new Set(s).add(e.draft!.id));
        this.loadLog();
        this.changed.emit(this.message(e, r.impact));
      },
      () => { this.busy.set(false); this.error.set('Could not apply the event.'); },
    );
  }

  dismiss(e: NewsEvent) {
    this.result.update((r) => (r ? { ...r, events: r.events.filter((x) => x !== e) } : r));
  }

  undo(ev: EventDraft) {
    this.api.undoEvent(ev.id).subscribe(() => {
      this.loadLog();
      this.changed.emit(`Undone: ${ev.description}`);
    });
  }

  reset() {
    this.stopFeed();
    this.api.resetLive().subscribe(() => {
      this.result.set(null);
      this.loadLog();
      this.changed.emit('Demo reset: all events and tasks removed.');
    });
  }

  /** Replays the sample headlines as a live feed: each one is analysed, and its linked events are applied after a pause. */
  async playFeed() {
    this.feeding.set(true);
    for (const s of this.samples()) {
      if (!this.feeding()) break;
      this.text.set(s.text);
      const r = await this.analyze();
      await new Promise((ok) => setTimeout(ok, 2200));
      if (!this.feeding() || !r) break;
      for (const e of r.events) if (e.draft && !this.applied().has(e.draft.id)) await this.apply(e);
      await new Promise((ok) => setTimeout(ok, 2500));
    }
    this.feeding.set(false);
  }

  stopFeed() { this.feeding.set(false); }

  private loadLog() {
    this.api.events().subscribe((l) => { this.log.set(l); this.applied.set(new Set(l.map((e) => e.id))); });
  }

  private message(e: NewsEvent, i: Impact) {
    const s = i.summary;
    return `${e.linked?.name}: ${this.eventLabel(e.event_type).toLowerCase()} applied. ${s.level_up} borrowers up a level, ${s.newly_watch} newly on contagion watch.`;
  }

  eventLabel = (t: string) => EVENT_LABELS[t] ?? t;
  signals = (codes: string[]) => codes.map((c) => SIGNAL_LABELS[c] ?? c).join(', ').toLowerCase();
  long = (m?: string) => (m ? monthLong.format(new Date(m + 'T00:00:00Z')) : '');
  when = (iso: string) => dateTimeFmt.format(new Date(iso));
  lvl = lvlClass;
  bn = bn;
}
