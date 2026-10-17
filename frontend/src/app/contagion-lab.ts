import { AfterViewInit, Component, ElementRef, OnDestroy, OnInit, computed, effect, inject, input, output, signal, viewChild } from '@angular/core';
import cytoscape from 'cytoscape';
import { Affected, Api, Candidate, ContagionMatrix, MatrixCell, Receiver, ShockNode, ShockResult, bn, lvlClass } from './api';
import { COLORS, SHAPES } from './graph-view';

const LATEST = '2026-09-01';
const monthLong = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const RELATIONS: Record<string, string> = {
  related: 'Related company', guarantor: 'Shared guarantor', transaction: 'Transaction partner', industry: 'Industry co-movement',
};
const STATE_COLORS: Record<string, string> = { seed: '#15171A', default: '#A3262F', hit: '#B07A1F' };

interface Preset { label: string; month?: string; seeds: 'top' | 'top2' | string[]; threshold?: number; }

/** What-if for contagion: default any node and watch the risk travel along the graph, wave by wave and round by round. */
@Component({
  selector: 'app-contagion-lab',
  template: `
  <section class="cc lab" aria-label="Contagion lab">
    <div class="cc-head">
      <div>
        <h2>Contagion lab <small>{{ long(month()) }}</small></h2>
        <p class="muted">Make a counterparty default and watch the risk spread through shared companies, guarantors and transaction partners.
          A borrower that the shock pushes over {{ threshold().toFixed(2) }} combined risk defaults in the next round and spreads it further. Nothing is saved.</p>
      </div>
    </div>

    <div class="presets" role="group" aria-label="Scenarios">
      @for (p of presets; track p.label) { <button type="button" class="preset" (click)="apply(p)">{{ p.label }}</button> }
      <button type="button" class="preset ghost" (click)="seeds.set([])">Clear</button>
    </div>

    <div class="stress-grid">
      <form class="ctrl" (submit)="$event.preventDefault()" aria-label="Shock controls">
        <h3>Who defaults</h3>
        <ul class="seed-list">
          @for (s of seeds(); track s) {
            <li><span>{{ labelOf(s) }}</span><button type="button" class="x" [attr.aria-label]="'Remove ' + labelOf(s)" (click)="toggleSeed(s)">×</button></li>
          } @empty { <li class="muted">Pick a counterparty below or a scenario above.</li> }
        </ul>
        <label for="cand">Add a counterparty</label>
        <select id="cand" name="cand" (change)="addFromSelect($event)">
          <option value="">Choose…</option>
          @for (c of candidates(); track c.id) {
            <option [value]="c.id">{{ c.label }} ({{ c.customers }} loans, {{ bn(c.exposure) }} bn{{ c.own > 0 ? ', already past due' : '' }})</option>
          }
        </select>
        <p class="muted tiny">Or click any node in the graph to add it.</p>

        <h3>Month</h3>
        <label class="sr" for="lab-month">Assessment month</label>
        <select id="lab-month" name="month" (change)="month.set($any($event.target).value)">
          @for (m of monthList(); track m) { <option [value]="m" [selected]="m === month()">{{ long(m) }}</option> }
        </select>

        <h3>Propagation</h3>
        <label for="alpha">Contagion strength α <b>{{ alpha().toFixed(2) }}</b></label>
        <input id="alpha" type="range" min="0.1" max="0.9" step="0.05" [value]="alpha()" (input)="alpha.set(+$any($event.target).value)" />
        <label for="hops">Path length <b>{{ hops() }} {{ hops() === 1 ? 'hop' : 'hops' }}</b></label>
        <input id="hops" type="range" min="1" max="3" step="1" [value]="hops()" (input)="hops.set(+$any($event.target).value)" />
        <label for="thr">Default threshold <b>{{ threshold().toFixed(2) }}</b></label>
        <input id="thr" type="range" min="0.5" max="0.95" step="0.05" [value]="threshold()" (input)="threshold.set(+$any($event.target).value)" />
        <p class="muted tiny">Lower threshold = a more fragile market, more dominoes fall.</p>
      </form>

      <div class="results" aria-live="polite">
        @if (res(); as r) {
          <dl class="lab-tiles">
            <div><dt>Borrowers hit</dt><dd>{{ r.summary.affected }}</dd></div>
            <div><dt>Domino defaults</dt><dd [class.neg]="r.summary.cascade_defaults">{{ r.summary.cascade_defaults }}</dd></div>
            <div><dt>Newly on contagion watch</dt><dd>{{ r.summary.new_watch }}</dd></div>
            <div><dt>Rule level ups</dt><dd>{{ r.summary.level_ups }}</dd></div>
            <div><dt>Exposure in default</dt><dd class="neg">{{ bn(r.summary.exposure_default) }}<small>bn VND</small></dd></div>
            <div><dt>Exposure touched</dt><dd>{{ bn(r.summary.exposure_affected) }}<small>bn VND</small></dd></div>
          </dl>
        }
        <div class="lab-graph">
          <div #host class="graph-host" role="img" [attr.aria-label]="graphLabel()"></div>
          <div class="chips" aria-hidden="true">
            <span class="chip"><i style="background:#15171A"></i>Defaults (shock)</span>
            <span class="chip"><i style="background:#A3262F"></i>Domino default</span>
            <span class="chip"><i style="background:#B07A1F"></i>Risk rises (size = increase)</span>
            <span class="chip"><i style="background:#9AA0A6"></i>Channel</span>
          </div>
          @if (running()) { <p class="hint">Simulating…</p> }
          @else if (res(); as r) {
            <p class="hint">Wave {{ wave() + 1 }} of {{ maxHop() + 1 }}
              @for (rd of r.rounds; track rd.round) { @if (rd.round > 0) { · round {{ rd.round }}: {{ rd.defaults.length }} domino {{ rd.defaults.length === 1 ? 'default' : 'defaults' }} } }
              <button type="button" class="link" (click)="animate()">Replay</button></p>
          } @else { <p class="hint">{{ error() || 'Pick who defaults to start.' }}</p> }
          @if (picked(); as p) {
            <div class="pick" role="dialog" aria-label="Selected node">
              <b>{{ p.label }}</b>
              <small>{{ p.type }}{{ p.hop !== null ? ', ' + p.hop + ' hops from the shock' : '' }}{{ p.delta ? ', risk +' + (p.delta * 100).toFixed(1) + ' pts' : '' }}</small>
              <div class="btns">
                @if (p.type !== 'Industry') {
                  <button type="button" class="btn ok" (click)="toggleSeed(p.id, p.label); picked.set(null)">{{ seeds().includes(p.id) ? 'Remove from shock' : 'Make it default too' }}</button>
                }
                @if (p.type === 'Customer') { <button type="button" class="btn ghost" (click)="openCase.emit({ id: p.id, month: month() })">Open case</button> }
              </div>
            </div>
          }
        </div>

        @if (res(); as r) {
          <div class="card pad-card">
            <h3>Who is hit <small>Click to open the case</small></h3>
            <div class="seg" role="group" aria-label="Filter by industry">
              <button type="button" [attr.aria-pressed]="!industry()" (click)="industry.set('')">All</button>
              @for (i of affectedIndustries(); track i) { <button type="button" [attr.aria-pressed]="industry() === i" (click)="industry.set(i)">{{ i }}</button> }
            </div>
            @for (a of shownAffected(); track a.id) {
              <button type="button" class="alert" (click)="openCase.emit({ id: a.id, month: month() })">
                <span class="dot {{ a.defaulted ? 'high' : lvl(a.level_after) }}" aria-hidden="true"></span>
                <span class="nm">{{ a.name }}
                  <small>{{ a.industry }}{{ a.hop !== null ? ', ' + a.hop + ' hops' : ', industry channel' }}{{ a.defaulted ? ', domino default in round ' + a.round : '' }}{{ a.level_after !== a.level_before ? ', ' + a.level_before + ' to ' + a.level_after : '' }}</small>
                </span>
                <span class="mini" aria-hidden="true"><i [style.width.%]="a.total_before * 100"></i><i class="up" [style.left.%]="a.total_before * 100" [style.width.%]="(a.total_after - a.total_before) * 100"></i></span>
                <span class="amt">+{{ (a.delta * 100).toFixed(1) }}<small>pts</small></span>
              </button>
            } @empty { <p class="muted">No borrower is affected at this threshold.</p> }
            @if (hiddenAffected()) { <p class="muted tiny">And {{ hiddenAffected() }} more.</p> }
          </div>
        }
      </div>
    </div>

    @if (mx(); as m) {
      <div class="card pad-card mx">
        <h3>Contagion matrix <small>Risk points flowing today from each source into each industry. Click a cell to see who receives it.</small></h3>
        <div class="seg" role="group" aria-label="Matrix view">
          <button type="button" [attr.aria-pressed]="mode() === 'spreaders'" (click)="mode.set('spreaders')">Top spreaders</button>
          <button type="button" [attr.aria-pressed]="mode() === 'channels'" (click)="mode.set('channels')">By channel</button>
        </div>
        <div class="mx-grid">
          <div class="mx-scroll">
            <table class="heat mx-t">
              <thead><tr><th scope="col">{{ mode() === 'spreaders' ? 'Source' : 'Channel' }}</th>@for (i of m.industries; track i) { <th scope="col">{{ i }}</th> }<th scope="col">Total</th></tr></thead>
              <tbody>
                @if (mode() === 'spreaders') {
                  @for (r of m.rows; track r.id) {
                    <tr [class.sel]="sel()?.id === r.id">
                      <th scope="row">{{ r.label }}<small>{{ r.type }}, {{ r.reach }} borrowers{{ r.own >= 1 ? ', in default' : '' }}</small></th>
                      @for (c of r.cells; track c.industry) {
                        <td [style.background]="heat(c.value, m.max_cell)">
                          @if (c.value) {
                            <button type="button" class="cell" [class.on]="sel()?.id === r.id && sel()?.industry === c.industry"
                                    [attr.aria-label]="r.label + ' to ' + c.industry + ': ' + c.value.toFixed(0) + ' points, ' + c.count + ' borrowers'"
                                    (click)="select(r.id, r.label, c.industry, r.own)">{{ c.value.toFixed(0) }}</button>
                          }
                        </td>
                      }
                      <td class="plain"><button type="button" class="cell" (click)="select(r.id, r.label, '', r.own)">{{ r.total.toFixed(0) }}</button></td>
                    </tr>
                  }
                } @else {
                  @for (r of m.channels; track r.relation) {
                    <tr>
                      <th scope="row">{{ relation(r.relation) }}</th>
                      @for (c of r.cells; track c.industry) { <td [style.background]="heat(c.value, maxChannel())">{{ c.value ? c.value.toFixed(0) : '' }}</td> }
                      <td class="plain">{{ r.total.toFixed(0) }}</td>
                    </tr>
                  }
                }
              </tbody>
              <tfoot><tr><th scope="row">All sources</th>@for (c of m.totals; track c.industry) { <td>{{ c.value.toFixed(0) }}</td> }<td></td></tr></tfoot>
            </table>
          </div>
          <aside class="mx-side" aria-live="polite">
            @if (sel(); as s) {
              <h3>{{ s.label }} <small>{{ s.industry ? 'into ' + s.industry : 'all industries' }}</small></h3>
              @for (r of shownReceivers(); track r.id) {
                <button type="button" class="alert" (click)="openCase.emit({ id: r.id, month: month() })">
                  <span class="dot {{ r.own >= 0.6 ? 'high' : r.own >= 0.3 ? 'mid' : 'low' }}" aria-hidden="true"></span>
                  <span class="nm">{{ r.name }}<small>{{ r.industry }}, {{ relation(r.relation) }}, {{ r.hops }} {{ r.hops === 1 ? 'hop' : 'hops' }}</small></span>
                  <span class="amt">{{ r.contribution.toFixed(1) }}<small>pts</small></span>
                </button>
              } @empty { <p class="muted">Loading…</p> }
              @if (s.own < 1) {
                <button type="button" class="btn ghost full" (click)="simulate(s.id, s.label)">Simulate {{ s.label }} defaulting</button>
              } @else {
                <p class="muted tiny">Already in default: its risk is fully counted. Ring-fence it from the borrowers above in Case review.</p>
              }
            } @else {
              <p class="muted">Each cell adds up the risk a source passes to borrowers of one industry (inflow × 100). The biggest spreaders
                are the counterparties to ring-fence first.</p>
            }
          </aside>
        </div>
      </div>
    }
  </section>`,
})
export class ContagionLab implements OnInit, AfterViewInit, OnDestroy {
  private api = inject(Api);
  months = input<string[]>([]);
  start = input<{ month: string; seeds: string[] } | null>(null);
  openCase = output<{ id: string; month: string }>();

  month = signal(LATEST);
  seeds = signal<string[]>([]);
  alpha = signal(0.5);
  hops = signal(2);
  threshold = signal(0.8);
  res = signal<ShockResult | null>(null);
  mx = signal<ContagionMatrix | null>(null);
  candidates = signal<Candidate[]>([]);
  running = signal(false);
  error = signal('');
  picked = signal<ShockNode | null>(null);
  industry = signal('');
  wave = signal(0);
  mode = signal<'spreaders' | 'channels'>('spreaders');
  sel = signal<{ id: string; label: string; industry: string; own: number } | null>(null);
  receivers = signal<Receiver[]>([]);
  private labels = new Map<string, string>();

  presets: Preset[] = [
    { label: 'Largest healthy counterparty defaults', seeds: 'top' },
    { label: 'Two counterparties default together', seeds: 'top2' },
    { label: 'Fragile market (threshold 0.6)', seeds: 'top', threshold: 0.6 },
    { label: "Customer A's partner fails, May 2026", month: '2026-05-01', seeds: ['CP002'] },
  ];

  monthList = computed(() => (this.months().length ? this.months() : [LATEST]));
  maxHop = computed(() => Math.max(0, ...(this.res()?.graph.nodes ?? []).map((n) => n.hop ?? 0)));
  affectedIndustries = computed(() => [...new Set((this.res()?.affected ?? []).map((a) => a.industry))].sort());
  private filtered = computed<Affected[]>(() => (this.res()?.affected ?? []).filter((a) => !this.industry() || a.industry === this.industry()));
  shownAffected = computed(() => this.filtered().slice(0, 25));
  hiddenAffected = computed(() => Math.max(0, this.filtered().length - 25));
  shownReceivers = computed(() => this.receivers().filter((r) => !this.sel()?.industry || r.industry === this.sel()!.industry).slice(0, 12));
  maxChannel = computed(() => Math.max(1, ...(this.mx()?.channels ?? []).flatMap((c) => c.cells.map((x: MatrixCell) => x.value))));
  graphLabel = computed(() => {
    const r = this.res();
    return r ? `Default cascade: ${r.summary.affected} borrowers hit, ${r.summary.cascade_defaults} domino defaults.` : 'Contagion graph, no shock yet';
  });

  private host = viewChild.required<ElementRef<HTMLElement>>('host');
  private cy?: cytoscape.Core;
  private timers: ReturnType<typeof setTimeout>[] = [];
  private reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  constructor() {
    effect(() => {
      const m = this.month();
      this.api.candidates(m).subscribe((c) => {
        this.candidates.set(c);
        c.forEach((x) => this.labels.set(x.id, x.label));
        if (!this.seeds().length && !this.start()) this.apply(this.presets[0]);
      });
      this.mx.set(null);
      this.api.matrix(m).subscribe((x) => this.mx.set(x));
    });
    // Debounced re-run whenever the scenario changes.
    effect((onCleanup) => {
      const body = { as_of: this.month(), seeds: this.seeds(), alpha: this.alpha(), hops: this.hops(), threshold: this.threshold() };
      if (!body.seeds.length) { this.res.set(null); this.draw(); return; }
      const t = setTimeout(() => {
        this.running.set(true);
        this.api.shock(body).subscribe({
          next: (r) => {
            this.running.set(false); this.error.set(''); this.picked.set(null);
            r.seeds.forEach((s) => this.labels.set(s.id, s.label));
            this.res.set(r);
            if (this.industry() && !r.affected.some((a) => a.industry === this.industry())) this.industry.set('');
            this.draw();
          },
          error: (e) => { this.running.set(false); this.res.set(null); this.draw(); this.error.set(e?.error?.detail ?? 'The simulation failed. Check the backend.'); },
        });
      }, 250);
      onCleanup(() => clearTimeout(t));
    });
  }

  ngOnInit() {
    const s = this.start();
    if (s) { this.month.set(s.month); this.seeds.set(s.seeds); }
  }

  ngAfterViewInit() {
    this.cy = cytoscape({
      container: this.host().nativeElement, wheelSensitivity: 0.25, pixelRatio: Math.max(2, window.devicePixelRatio || 1),
      style: [
        { selector: 'node', style: {
          label: 'data(label)', 'font-family': 'Hanken Grotesk, system-ui, sans-serif', 'font-size': 9, color: '#15171A',
          'text-valign': 'bottom', 'text-margin-y': 4, 'background-color': 'data(color)', shape: 'data(shape)' as never,
          width: 'data(size)', height: 'data(size)', 'border-width': 2, 'border-color': '#ffffff', 'text-wrap': 'ellipsis', 'text-max-width': '110px',
          'text-background-color': '#ffffff', 'text-background-opacity': 0.85, 'text-background-padding': '2px', 'text-background-shape': 'roundrectangle',
          'transition-property': 'opacity, text-opacity', 'transition-duration': 350 as never,
        } },
        { selector: 'node[state = "path"]', style: { 'font-size': 8, color: '#6A7077' } },
        { selector: 'node[state = "seed"]', style: { 'font-size': 11, 'font-weight': 700, 'underlay-color': '#A3262F', 'underlay-opacity': 0.25, 'underlay-padding': 10, 'underlay-shape': 'ellipse' } },
        { selector: 'node[state = "default"]', style: { 'border-color': '#A3262F', 'border-width': 3, 'underlay-color': '#A3262F', 'underlay-opacity': 0.18, 'underlay-padding': 6, 'underlay-shape': 'ellipse' } },
        { selector: 'node.boom', style: { 'underlay-padding': 16, 'underlay-opacity': 0.35 } },
        { selector: 'node.picked', style: { 'border-color': '#15171A', 'border-width': 4 } },
        { selector: 'node.dim', style: { opacity: 0.07, 'text-opacity': 0 } },
        { selector: 'edge', style: {
          width: 1.3, 'line-color': '#C9CDD2', 'target-arrow-color': '#C9CDD2', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.7,
          'curve-style': 'bezier', 'transition-property': 'opacity, line-color', 'transition-duration': 350 as never,
        } },
        { selector: 'edge.hot', style: { 'line-color': '#A3262F', 'target-arrow-color': '#A3262F', width: 2, opacity: 0.75 } },
        { selector: 'edge.dim', style: { opacity: 0.04 } },
      ],
    });
    this.cy.on('tap', 'node', (e) => {
      this.cy!.nodes().removeClass('picked');
      e.target.addClass('picked');
      this.picked.set(e.target.data('raw'));
    });
    this.cy.on('tap', (e) => { if (e.target === this.cy) { this.cy!.nodes().removeClass('picked'); this.picked.set(null); } });
    this.draw();
  }

  ngOnDestroy() {
    this.timers.forEach(clearTimeout);
    this.cy?.destroy();
  }

  private draw() {
    const cy = this.cy, r = this.res();
    if (!cy) return;
    this.timers.forEach(clearTimeout);
    cy.elements().remove();
    if (!r) return;
    cy.add([
      ...r.graph.nodes.map((n) => ({ data: {
        id: n.id, label: n.label, raw: n, hop: n.hop ?? 9, state: n.state, shape: SHAPES[n.type] ?? 'ellipse',
        color: STATE_COLORS[n.state] ?? COLORS[n.type] ?? '#9AA0A6',
        size: n.state === 'seed' ? 34 : n.state === 'default' ? 24 : n.state === 'hit' ? 12 + Math.min(1, (n.delta ?? 0) / 0.3) * 16 : 11,
      } })),
      ...r.graph.edges.map((e) => ({ data: { id: `${e.source}|${e.target}`, source: e.source, target: e.target, type: e.type } })),
    ]);
    cy.layout({ name: 'concentric', concentric: (n: cytoscape.NodeSingular) => 10 - Math.min(9, n.data('hop')),
                levelWidth: () => 1, minNodeSpacing: 22, animate: false, padding: 40 } as never).run();
    if (cy.zoom() > 1.15) { cy.zoom(1.15); cy.center(); } // a small cascade would otherwise be blown up to giant labels
    this.animate();
  }

  /** Reveals the cascade wave by wave (distance from the shock), then flashes the domino defaults. */
  animate() {
    const cy = this.cy;
    if (!cy || cy.nodes().empty()) return;
    this.timers.forEach(clearTimeout);
    this.timers = [];
    const max = this.maxHop();
    const show = (s: number) => {
      cy.nodes().filter((n) => n.data('hop') <= s).removeClass('dim');
      const edges = cy.edges().filter((e) => e.target().data('hop') <= s);
      edges.removeClass('dim');
      edges.filter((e) => ['hit', 'default'].includes((e as cytoscape.EdgeSingular).target().data('state'))).addClass('hot');
      this.wave.set(s);
    };
    if (this.reduceMotion) { show(max); return; }
    cy.elements().addClass('dim').removeClass('hot');
    for (let s = 0; s <= max; s++) this.timers.push(setTimeout(() => show(s), 200 + s * 750));
    this.timers.push(setTimeout(() => cy.nodes('[state = "default"]').flashClass('boom', 1100), 200 + (max + 1) * 750));
  }

  apply(p: Preset) {
    const healthy = this.candidates().filter((c) => c.own === 0);
    if (p.month) this.month.set(p.month);
    this.threshold.set(p.threshold ?? 0.8);
    this.seeds.set(p.seeds === 'top' ? healthy.slice(0, 1).map((c) => c.id) : p.seeds === 'top2' ? healthy.slice(0, 2).map((c) => c.id) : p.seeds);
  }

  toggleSeed(id: string, label?: string) {
    if (label) this.labels.set(id, label);
    this.seeds.update((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  addFromSelect(e: Event) {
    const el = e.target as HTMLSelectElement;
    if (el.value && !this.seeds().includes(el.value)) this.toggleSeed(el.value);
    el.value = '';
  }

  select(id: string, label: string, industry: string, own: number) {
    const prev = this.sel();
    this.sel.set({ id, label, industry, own });
    if (prev?.id !== id) {
      this.receivers.set([]);
      this.api.receivers(id, this.month()).subscribe((r) => this.receivers.set(r));
    }
  }

  simulate(id: string, label: string) {
    this.labels.set(id, label);
    this.seeds.set([id]);
    window.scrollTo({ top: 0, behavior: this.reduceMotion ? 'auto' : 'smooth' });
  }

  heat(v: number, max: number) {
    return v ? `rgb(163 38 47 / ${(0.06 + Math.min(1, v / Math.max(1, max)) * 0.5).toFixed(2)})` : 'transparent';
  }
  labelOf = (id: string) => this.labels.get(id) ?? id;
  relation = (r: string) => RELATIONS[r] ?? r;
  long = (m?: string) => (m ? monthLong.format(new Date(m + 'T00:00:00Z')) : '');
  lvl = lvlClass;
  bn = bn;
}
