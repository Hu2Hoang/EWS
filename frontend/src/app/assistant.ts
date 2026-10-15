import { Component, ElementRef, inject, output, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, ChatTurn } from './api';

interface Msg { role: 'user' | 'model'; text: string; cited?: { id: string; name: string }[]; tools?: string[]; source?: string; masked?: number; }

const SUGGESTIONS = [
  'Which Seafood customers are High risk and why?',
  'Who shares a related company with Customer A?',
  'What happens if Real Estate revenue drops 25%?',
  'Summarise the portfolio risk today.',
];

/** Floating portfolio assistant. Gemini answers by calling typed tools, so every number comes from the rules engine. */
@Component({
  selector: 'app-assistant',
  imports: [FormsModule],
  template: `
  <button type="button" class="fab" [attr.aria-expanded]="open()" aria-controls="assistant-panel" (click)="open.set(!open())">
    Ask Gemini
  </button>
  @if (open()) {
    <section id="assistant-panel" class="chat" role="dialog" aria-label="Portfolio assistant">
      <header>
        <b>Portfolio assistant</b>
        <button type="button" class="x" aria-label="Close assistant" (click)="open.set(false)">×</button>
      </header>
      <div class="msgs" #scroller aria-live="polite">
        @if (!msgs().length) {
          <p class="muted">Ask about customers, shared exposure or what-if scenarios. Answers use only tool results from the graph and the rules engine.</p>
          @for (s of suggestions; track s) { <button type="button" class="sugg" (click)="send(s)">{{ s }}</button> }
        }
        @for (m of msgs(); track $index) {
          <div class="msg {{ m.role }}">
            <p>{{ m.text }}</p>
            @if (m.masked) { <small class="priv">PII masked before the LLM ({{ m.masked }} values)</small> }
            @if (m.tools?.length) { <div class="tools">@for (t of m.tools; track $index) { <code translate="no">{{ t }}</code> }</div> }
            @if (m.cited?.length) {
              <div class="cites">
                @for (c of m.cited; track c.id) { <button type="button" class="link" (click)="openCase.emit(c.id)">{{ c.name }}</button> }
              </div>
            }
          </div>
        }
        @if (busy()) { <div class="msg model"><p class="muted">Thinking…</p></div> }
      </div>
      <form (submit)="$event.preventDefault(); send(draft())">
        <label class="sr" for="chat-in">Your question</label>
        <input id="chat-in" name="question" autocomplete="off" placeholder="Ask about the portfolio…" [ngModel]="draft()" (ngModelChange)="draft.set($event)" />
        <button type="submit" class="btn ok" [disabled]="busy() || !draft().trim()">Send</button>
      </form>
    </section>
  }`,
})
export class Assistant {
  private api = inject(Api);
  openCase = output<string>();
  open = signal(false);
  busy = signal(false);
  draft = signal('');
  msgs = signal<Msg[]>([]);
  suggestions = SUGGESTIONS;
  private scroller = viewChild<ElementRef<HTMLElement>>('scroller');

  send(q: string) {
    q = q.trim();
    if (!q || this.busy()) return;
    const history: ChatTurn[] = this.msgs().map((m) => ({ role: m.role, text: m.text }));
    this.msgs.update((m) => [...m, { role: 'user', text: q }]);
    this.draft.set('');
    this.busy.set(true);
    this.scrollDown();
    this.api.chat(q, '2026-09-01', history).subscribe({
      next: (r) => {
        this.msgs.update((m) => [...m, {
          role: 'model', text: r.answer, cited: r.cited, source: r.source, masked: r.privacy?.replacements,
          tools: r.tool_calls.map((t) => `${t.tool}(${Object.values(t.args).join(', ')})`),
        }]);
        this.busy.set(false);
        this.scrollDown();
      },
      error: () => {
        this.msgs.update((m) => [...m, { role: 'model', text: 'The assistant could not be reached. Check the backend and try again.' }]);
        this.busy.set(false);
      },
    });
  }

  private scrollDown() {
    setTimeout(() => { const el = this.scroller()?.nativeElement; if (el) el.scrollTop = el.scrollHeight; });
  }
}
