import { AfterViewInit, Component, ElementRef, OnDestroy, computed, effect, input, output, viewChild } from '@angular/core';
import cytoscape from 'cytoscape';
import { Context, Overview } from './api';

const COLORS: Record<string, string> = {
  Customer: '#15171A', Loan: '#4F6D8F', Collateral: '#5F8F7B', Industry: '#B58A3C',
  Company: '#9AA0A6', Guarantor: '#6E9AA8', RiskEvent: '#A3262F',
};
const SHAPES: Record<string, string> = {
  Customer: 'round-rectangle', Loan: 'diamond', Collateral: 'hexagon', Industry: 'round-triangle',
  Company: 'ellipse', Guarantor: 'ellipse', RiskEvent: 'star',
};

/** The full Knowledge Graph is the dimmed backdrop; the Context Graph of the current decision lights up. */
@Component({
  selector: 'app-graph-view',
  template: `<div #host class="graph-host" role="img" [attr.aria-label]="label()"></div>`,
  styles: [`:host { display: block; height: 100%; } .graph-host { width: 100%; height: 100%; }`],
})
export class GraphView implements AfterViewInit, OnDestroy {
  overview = input<Overview | null>(null);
  context = input<Context | null>(null);
  focusId = input<string | null>(null);
  nodeClick = output<string>();

  /** Text alternative for the canvas; the side panel lists the same signals as buttons. */
  label = computed(() => {
    const c = this.context();
    if (!c) return 'Knowledge graph, loading';
    const flagged = c.graph.nodes.filter((n) => n.flagged).length;
    return `Context graph for ${c.customer.name}: ${c.graph.nodes.length} nodes, ${flagged} with early-warning signals.`;
  });
  private reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  private host = viewChild.required<ElementRef<HTMLElement>>('host');
  private cy?: cytoscape.Core;
  private lastCustomer = '';
  private added = new Set<string>(); // context-only elements (loan, collateral, events)
  private moved = new Map<string, { x: number; y: number }>(); // backdrop nodes moved into the context layout

  constructor() {
    effect(() => {
      const ov = this.overview();
      const ctx = this.context();
      if (this.cy && ov) this.sync(ov, ctx);
    });
    effect(() => {
      const id = this.focusId();
      if (!this.cy || !id) return;
      const n = this.cy.getElementById(id);
      if (n.nonempty()) {
        this.cy.animate({ center: { eles: n }, zoom: Math.max(this.cy.zoom(), 1.1) }, { duration: this.reduceMotion ? 0 : 350 });
        n.flashClass('pulse', 1200);
      }
    });
  }

  ngAfterViewInit() {
    this.cy = cytoscape({
      container: this.host().nativeElement,
      wheelSensitivity: 0.25,
      pixelRatio: Math.max(2, window.devicePixelRatio || 1), // supersample so labels stay crisp when the view is fitted/zoomed out
      textureOnViewport: false, hideEdgesOnViewport: false,
      style: [
        { selector: 'node', style: {
          label: 'data(label)', 'font-family': 'Hanken Grotesk, system-ui, sans-serif', 'font-size': 9, 'font-weight': 500,
          'text-valign': 'bottom', 'text-margin-y': 4, 'background-color': 'data(color)', shape: 'data(shape)' as never,
          width: 11, height: 11, color: '#15171A', 'text-wrap': 'wrap', 'text-max-width': '96px',
          opacity: 0.16, 'text-opacity': 0, 'border-width': 2, 'border-color': '#ffffff',
          'text-background-color': '#ffffff', 'text-background-opacity': 0.85, 'text-background-padding': '2px',
          'text-background-shape': 'roundrectangle',
        } },
        { selector: 'node[type = "Customer"]', style: { width: 13, height: 13 } },
        { selector: 'node.ctx', style: { width: 26, height: 26 } },
        { selector: 'node.ctx', style: { opacity: 1, 'text-opacity': 1, 'font-size': 11, 'font-weight': 500 } },
        { selector: 'node.focusCustomer', style: {
          width: 40, height: 40, 'font-size': 12, 'font-weight': 700,
          'underlay-color': '#15171A', 'underlay-opacity': 0.28, 'underlay-padding': 9, 'underlay-shape': 'ellipse',
        } },
        { selector: 'node.flag', style: {
          'border-width': 3, 'border-color': '#A3262F',
          'underlay-color': '#A3262F', 'underlay-opacity': 0.2, 'underlay-padding': 7, 'underlay-shape': 'ellipse',
        } },
        { selector: 'node.pulse', style: { 'border-width': 5, 'border-color': '#15171A' } },
        { selector: 'edge', style: {
          width: 1, 'line-color': '#C9CDD2', opacity: 0.12, 'curve-style': 'bezier',
          'target-arrow-shape': 'triangle', 'target-arrow-color': '#C9CDD2', 'arrow-scale': 0.7,
        } },
        { selector: 'edge.ctx', style: {
          'line-style': 'dashed', 'line-dash-pattern': [7, 5],
          opacity: 0.9, width: 1.6, 'line-color': '#8E959C', 'target-arrow-color': '#8E959C', label: 'data(type)',
          'font-size': 8, color: '#6A7077', 'text-rotation': 'autorotate', 'text-background-color': '#ffffff',
          'text-background-opacity': 0.85, 'text-background-padding': '1px',
        } },
        { selector: 'edge.ctx.hot', style: { 'line-color': '#A3262F', 'target-arrow-color': '#A3262F', width: 2 } },
      ],
    });
    this.startFlow();
    this.cy.on('tap', 'node.ctx', (e) => this.nodeClick.emit(e.target.id()));
    const ov = this.overview();
    if (ov) this.sync(ov, this.context());
  }

  ngOnDestroy() {
    cancelAnimationFrame(this.raf);
    this.cy?.destroy();
  }

  /** Marching dashes on context edges show the direction of the relationship (source to target). Skipped when motion is reduced. */
  private raf = 0;
  private startFlow() {
    if (this.reduceMotion) return;
    let offset = 0;
    const tick = () => {
      offset = (offset + 0.35) % 12;
      this.cy?.edges('.ctx').style('line-dash-offset', -offset);
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  private sync(ov: Overview, ctx: Context | null) {
    const cy = this.cy!;
    if (cy.nodes().empty()) {
      cy.add([
        ...ov.nodes.map((n) => ({ data: { id: n.id, label: n.label, type: n.type, color: COLORS[n.type], shape: SHAPES[n.type] } })),
        ...ov.edges.map((e) => ({ data: { id: this.eid(e), source: e.source, target: e.target, type: e.type } })),
      ]);
      cy.layout({ name: 'cose', animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 70, randomize: true } as never).run();
    }
    cy.elements().removeClass('ctx flag focusCustomer hot');
    if (!ctx) return;

    // drop context-only elements from the previous state (events of another month, another customer)
    const want = new Set<string>([
      ...ctx.graph.nodes.map((n) => n.id),
      ...ctx.graph.edges.map((e) => this.eid(e)),
    ]);
    for (const id of [...this.added]) {
      if (!want.has(id)) { cy.getElementById(id).remove(); this.added.delete(id); }
    }
    // Put back backdrop nodes that the previous context moved.
    for (const [id, pos] of this.moved) cy.getElementById(id).position(pos);
    this.moved.clear();

    // Structured layout: customer in the middle, loan and collateral to the right, related parties to the left,
    // industry below, risk events above. For a customer outside the sampled backdrop, anchor at the view centre.
    const anchor = cy.getElementById(ctx.customer.id);
    const c = anchor.nonempty() ? { ...anchor.position() } : { x: cy.width() / 2, y: cy.height() / 2 };
    const slots: Record<string, number> = {};
    const offset = (type: string): [number, number] => {
      const k = (slots[type] = (slots[type] ?? -1) + 1);
      switch (type) {
        case 'Loan': return [160, -30];
        case 'Collateral': return [300, 10];
        case 'Industry': return [10, 180];
        case 'Company': return [-190, -50 + k * 95];
        case 'Guarantor': return [-150, 150 + k * 70];
        case 'RiskEvent': return [-250 + k * 150, -200 - (k % 2) * 45];
        default: return [0, 0];
      }
    };
    for (const n of ctx.graph.nodes) {
      const [dx, dy] = offset(n.type);
      const pos = { x: c.x + dx, y: c.y + dy };
      let el = cy.getElementById(n.id);
      if (el.empty()) {
        el = cy.add({ data: { id: n.id, label: n.label, type: n.type, color: COLORS[n.type], shape: SHAPES[n.type] }, position: pos });
        this.added.add(n.id);
      } else {
        if (!this.added.has(n.id)) this.moved.set(n.id, { ...el.position() });
        el.position(pos);
      }
    }
    for (const e of ctx.graph.edges) {
      const id = this.eid(e);
      if (cy.getElementById(id).empty()) {
        cy.add({ data: { id, source: e.source, target: e.target, type: e.type } });
        this.added.add(id);
      }
    }
    for (const n of ctx.graph.nodes) {
      const el = cy.getElementById(n.id);
      el.data('label', n.label);
      el.addClass('ctx');
      if (n.flagged) el.addClass('flag');
    }
    const flaggedIds = new Set(ctx.graph.nodes.filter((n) => n.flagged).map((n) => n.id));
    for (const e of ctx.graph.edges) {
      const el = cy.getElementById(this.eid(e)).addClass('ctx');
      if (flaggedIds.has(e.source) || flaggedIds.has(e.target)) el.addClass('hot');
    }
    cy.getElementById(ctx.customer.id).addClass('focusCustomer');

    if (this.lastCustomer !== ctx.customer.id) {
      this.lastCustomer = ctx.customer.id;
      const ctxEles = cy.elements('.ctx');
      cy.animate({ fit: { eles: ctxEles, padding: 70 } }, { duration: this.reduceMotion ? 0 : 500 });
    }
  }

  private eid = (e: { source: string; target: string; type: string }) => `${e.source}|${e.target}|${e.type}`;
}
