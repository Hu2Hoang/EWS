// Records the demo video: drives the app through the scenes in scenes.json, paced by the voice-over durations,
// with an animated cursor, highlight boxes and captions. Frames come from Chrome's screencast (CDP) at 1920x1200.
//
// Usage:  node record.mjs [APP_URL]          default http://localhost:4200 (dev server, API on :8000)
//         node record.mjs https://<cloud-run-url>
// Then:   python build.py   (assembles out/demo.mp4)
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const OUT = path.join(HERE, 'out');
const FRAMES = path.join(OUT, 'frames');
const BASE = (process.argv[2] ?? 'http://localhost:4200').replace(/\/$/, '');
const API = process.env.API ?? (new URL(BASE).hostname === 'localhost' ? 'http://localhost:8000' : `${BASE}/api`);
const scenes = JSON.parse(fs.readFileSync(path.join(HERE, 'scenes.json'), 'utf8'));
const durations = JSON.parse(fs.readFileSync(path.join(OUT, 'durations.json'), 'utf8'));
const now = () => Date.now() / 1000;
const sleep = (s) => new Promise((ok) => setTimeout(ok, Math.max(0, s) * 1000));

// Overlay helpers injected into the page: fake cursor, highlight box, caption, URL badge, end card.
const OVERLAY = () => {
  const css = `
    #dm-cursor{position:fixed;z-index:99999;left:0;top:0;width:22px;height:22px;margin:-3px 0 0 -3px;pointer-events:none;
      transition:transform .55s cubic-bezier(.4,.1,.2,1);filter:drop-shadow(0 2px 3px rgb(0 0 0/.35))}
    #dm-cursor.press::after{content:'';position:absolute;left:-9px;top:-9px;width:40px;height:40px;border-radius:50%;
      background:rgb(232 163 61/.45);animation:dmp .45s ease-out forwards}
    @keyframes dmp{from{transform:scale(.3);opacity:1}to{transform:scale(1.2);opacity:0}}
    .dm-hl{position:fixed;z-index:99990;pointer-events:none;border:3px solid #E8A33D;border-radius:10px;
      box-shadow:0 0 0 4px rgb(232 163 61/.25),0 8px 30px rgb(232 163 61/.35);animation:dmi .35s ease-out}
    @keyframes dmi{from{opacity:0;transform:scale(1.04)}to{opacity:1;transform:none}}
    #dm-cap{position:fixed;z-index:99995;left:24px;bottom:24px;max-width:430px;background:#15171A;color:#fff;
      font:500 15px/1.35 'Hanken Grotesk',system-ui,sans-serif;padding:12px 18px;border-radius:10px;
      box-shadow:0 10px 30px rgb(0 0 0/.25);transition:opacity .3s}
    #dm-cap:empty{opacity:0}
    #dm-url{position:fixed;z-index:99995;left:24px;bottom:82px;background:#fff;color:#3E7B62;border:1px solid #d5e5dc;
      font:500 12px 'Hanken Grotesk',system-ui,sans-serif;padding:5px 12px;border-radius:999px}
    #dm-url::before{content:'● ';color:#3E7B62}
    #dm-end{position:fixed;inset:0;z-index:100000;background:#fff;display:grid;place-content:center;text-align:center;gap:18px;
      font-family:'Hanken Grotesk',system-ui,sans-serif;animation:dmi .6s ease-out}
    #dm-end h1{font:300 64px/1.05 Newsreader,Georgia,serif;margin:0;color:#15171A}
    #dm-end p{margin:0;color:#6A7077;font-size:20px} #dm-end b{color:#15171A;font-weight:500}`;
  const ensure = () => {
    if (document.getElementById('dm-cursor')) return;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const c = document.createElement('div'); c.id = 'dm-cursor';
    c.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 2l16 9.5-7 1.6-3.6 6.4z" fill="#15171A" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    c.style.transform = 'translate(720px,450px)';
    document.body.appendChild(c);
    const cap = document.createElement('div'); cap.id = 'dm-cap'; document.body.appendChild(cap);
  };
  window.__dm = {
    cursorTo(x, y) { ensure(); document.getElementById('dm-cursor').style.transform = `translate(${x}px,${y}px)`; },
    press() { const c = document.getElementById('dm-cursor'); c.classList.remove('press'); void c.offsetWidth; c.classList.add('press'); },
    highlight(b, pad) {
      ensure();
      const d = document.createElement('div'); d.className = 'dm-hl';
      Object.assign(d.style, { left: `${b.x - pad}px`, top: `${b.y - pad}px`, width: `${b.width + 2 * pad}px`, height: `${b.height + 2 * pad}px` });
      document.body.appendChild(d);
    },
    clear() { document.querySelectorAll('.dm-hl').forEach((e) => e.remove()); },
    caption(t) { ensure(); document.getElementById('dm-cap').textContent = t; },
    url(u) {
      ensure();
      let e = document.getElementById('dm-url');
      if (!e) { e = document.createElement('div'); e.id = 'dm-url'; document.body.appendChild(e); }
      e.textContent = `Live: ${u}`;
    },
    end(lines) {
      const e = document.createElement('div'); e.id = 'dm-end';
      e.innerHTML = lines.map(([tag, t]) => `<${tag}>${t}</${tag}>`).join('');
      document.body.appendChild(e);
    },
  };
};

// A real window size and scale factor, not Playwright's viewport emulation: Chrome's screencast ignores the emulation,
// and headless Chrome still reserves about 87 px for browser UI, so the window is taller than the 900 px page.
const browser = await chromium.launch({ executablePath: process.env.CHROME ?? '/usr/bin/google-chrome', headless: true,
                                        args: ['--no-sandbox', '--hide-scrollbars', '--window-size=1440,987', '--force-device-scale-factor=1.3333333'] });
const ctx = await browser.newContext({ viewport: null });
await ctx.addInitScript(OVERLAY);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

// --- helpers ---------------------------------------------------------------------------------------------------
const box = async (loc) => { await loc.scrollIntoViewIfNeeded(); return loc.boundingBox(); };
async function hl(loc, pad = 8) {
  const b = await box(loc);
  if (b) await page.evaluate(([b, pad]) => window.__dm.highlight(b, pad), [b, pad]);
}
const clearHl = () => page.evaluate(() => window.__dm.clear());
async function moveTo(loc) {
  const b = await box(loc);
  await page.evaluate(([x, y]) => window.__dm.cursorTo(x, y), [b.x + b.width / 2, b.y + b.height / 2]);
  await sleep(0.6);
  return b;
}
async function click(loc) {
  await moveTo(loc);
  await page.evaluate(() => window.__dm.press());
  await loc.click();
}
async function key(loc, k, times = 1) {
  await moveTo(loc);
  await loc.focus();
  for (let i = 0; i < times; i++) { await page.keyboard.press(k); await sleep(0.25); }
}
const tab = (name) => page.locator('nav.tabs button', { hasText: name });

// --- screencast --------------------------------------------------------------------------------------------------
fs.rmSync(FRAMES, { recursive: true, force: true });
fs.mkdirSync(FRAMES, { recursive: true });
const cdp = await ctx.newCDPSession(page);
const frames = [];
cdp.on('Page.screencastFrame', async (f) => {
  const name = `f${String(frames.length).padStart(6, '0')}.jpg`;
  cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});  // ack first: Chrome waits for it
  frames.push({ name, ts: f.metadata.timestamp });
  fs.writeFileSync(path.join(FRAMES, name), Buffer.from(f.data, 'base64'));
});

// --- clean state, open the app -----------------------------------------------------------------------------------
await fetch(`${API}/live/reset`, { method: 'POST' });
await page.goto(`${BASE}/?v=overview&c=C_A&m=2026-05`);
await page.waitForSelector('.tiles', { timeout: 60000 });
await page.waitForSelector('nav.tabs');
await sleep(2);
await page.evaluate((u) => window.__dm.url(u), BASE.replace(/^https?:\/\//, ''));
await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 88, maxWidth: 1920, maxHeight: 1200, everyNthFrame: 1 });
await sleep(1);

const marks = [];
async function scene(id, fn) {
  const s = scenes.find((x) => x.id === id);
  const start = now();
  marks.push({ id, t: start });
  console.log(`▶ ${id} (${durations[id]}s)`);
  await page.evaluate((c) => window.__dm.caption(c), s.caption);
  const at = (sec) => sleep(start + sec - now());
  await fn(at);
  const over = now() - start - durations[id];
  if (over > 0) console.log(`  ⚠ actions ran ${over.toFixed(1)}s past the voice-over`);
  await sleep(start + durations[id] + 0.45 - now());
  await clearHl();
}

// --- scenes ------------------------------------------------------------------------------------------------------
await scene('s01_intro', async (at) => {
  await at(1); await hl(page.locator('.tiles'));
  await at(8.5); await clearHl(); await hl(page.locator('.cc-grid > .card').first());
  await key(page.locator('#cc-month'), 'Home');  // play from the first month, not from the current one
  await at(10.5); await click(page.getByRole('button', { name: 'Play timeline' }));
});

await scene('s02_case', async (at) => {
  await at(0.3); await click(tab('Case review'));
  await page.waitForSelector('.panel .sig', { timeout: 30000 });
  await at(3.5); await hl(page.locator('.graph'), 0);
  await at(9); await clearHl(); await key(page.locator('#scrub'), 'ArrowLeft');
  await at(11); await key(page.locator('#scrub'), 'ArrowRight');
  await page.waitForTimeout(1200);
  await at(12.5); await hl(page.locator('.panel .sig').first().locator('..'), 4);
  await at(16.5); await clearHl(); await click(page.locator('.panel .sig', { hasText: 'Related company' }));
});

await scene('s03_gemini', async (at) => {
  const run = page.getByRole('button', { name: 'Run AI analysis' });
  if (await run.isVisible().catch(() => false)) await click(run);
  await page.waitForSelector('.panel .ai', { timeout: 60000 });
  await at(3); await hl(page.locator('.panel .ai .steps'));
  await at(8.5); await clearHl(); await hl(page.locator('.panel .ai .find').first());
  await at(14); await clearHl(); await hl(page.locator('.panel .ai .src'));
});

await scene('s04_contagion', async (at) => {
  await at(2); await click(page.locator('.graph .sim'));
  await page.waitForSelector('.lab-tiles', { timeout: 30000 });
  await at(6.5); await hl(page.locator('.lab-graph'), 0);
  await at(12.5); await clearHl(); await hl(page.locator('.lab-tiles'));
});

await scene('s05_domino', async (at) => {
  await at(1); await page.locator('#lab-month').selectOption('2026-09-01');
  await sleep(1.2);
  await click(page.getByRole('button', { name: 'Largest healthy counterparty defaults' }));
  await page.waitForSelector('.lab-tiles', { timeout: 30000 });
  await at(5.5); await hl(page.locator('.lab-graph'), 0);
  await at(10); await clearHl(); await hl(page.locator('.lab-tiles > div').nth(1));
  await at(13.5); await clearHl(); await key(page.locator('#alpha'), 'ArrowRight', 3);
});

await scene('s06_matrix', async (at) => {
  await at(0.3); await page.locator('.mx').scrollIntoViewIfNeeded();
  await page.evaluate(() => document.querySelector('.mx')?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  await sleep(0.8);
  await hl(page.locator('.mx-scroll'));
  // the darkest cell of the top row
  const cells = page.locator('.mx-t tbody tr').first().locator('.cell');
  const vals = await cells.allInnerTexts();
  const best = vals.slice(0, -1).map(Number).reduce((bi, v, i, a) => (v > a[bi] ? i : bi), 0);
  await at(5); await clearHl(); await click(cells.nth(best));
  await at(7.5); await hl(page.locator('.mx-side'));
});

await scene('s07_news', async (at) => {
  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await at(0.3); await click(tab('News to graph'));
  const sample = page.locator('.samples .preset', { hasText: 'Vietnamese' });
  await sample.waitFor({ timeout: 30000 });
  await at(2); await click(sample);
  await page.waitForSelector('.news-ev', { timeout: 60000 });
  await at(6.5); await hl(page.locator('.news-ev').first().locator('.linked'));
  await at(11); await clearHl(); await hl(page.locator('.news-ev').first().locator('.impact-list'));
  await at(15); await clearHl(); await click(page.getByRole('button', { name: 'Apply to graph' }).first());
  await page.waitForSelector('.ok-note', { timeout: 30000 });
  await hl(page.locator('.toast'));
});

await scene('s08_act', async (at) => {
  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await at(0.3); await click(tab('Case review'));
  await page.waitForSelector('.add-task', { timeout: 30000 });
  await sleep(1);
  const sel = page.locator('#new-action');
  await at(3); await moveTo(sel); await sel.selectOption('request_additional_collateral');
  await click(page.getByRole('button', { name: 'Add task' }));
  await at(8); await sel.selectOption('review_related_exposure');
  await click(page.getByRole('button', { name: 'Add task' }));
  await sleep(1.2);
  await at(11); await hl(page.locator('.panel .act', { hasText: 'Ring-fence' }).last());
});

await scene('s09_board', async (at) => {
  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await at(0.3); await click(tab('Action board'));
  await page.waitForSelector('.kanban .task', { timeout: 30000 });
  const card = (t) => page.locator('.task', { hasText: t });
  await at(2); await hl(card('Request Additional Collateral'));
  await at(4); await clearHl();
  await moveTo(card('Request Additional Collateral'));
  await card('Request Additional Collateral').dragTo(page.locator('.col').nth(2));
  await page.locator('.col').nth(2).locator('.task', { hasText: 'Request Additional Collateral' }).locator('.result').waitFor({ timeout: 30000 });
  await at(6.5); await hl(page.locator('.col').nth(2).locator('.task', { hasText: 'Request Additional Collateral' }));
  await at(10.5); await clearHl();
  await click(card('Ring-fence Related Exposure').getByRole('button', { name: 'Complete' }));
  await page.locator('.col').nth(2).locator('.task', { hasText: 'Ring-fence' }).locator('.result').waitFor({ timeout: 30000 });
  await at(12); await hl(page.locator('.col').nth(2).locator('.task', { hasText: 'Ring-fence' }));
  await at(17.5); await clearHl(); await hl(page.locator('.tile').nth(3));
});

await scene('s10_assistant', async (at) => {
  await at(0.5); await click(page.locator('.fab'));
  await at(2); await click(page.locator('.sugg', { hasText: 'shares a related company' }));
  await page.locator('.msg.model').last().locator('.cites, p').first().waitFor({ timeout: 60000 });
  await page.waitForFunction(() => !document.querySelector('.msgs .muted')?.textContent?.includes('Thinking'), null, { timeout: 60000 });
  await sleep(0.5);
  await hl(page.locator('.msg.model').last());
});

await scene('s11_close', async (at) => {
  await at(0.3); await clearHl(); await click(page.locator('.chat .x'));
  await at(1); await hl(page.locator('.kpis'));
  await at(13.5); await clearHl();
  await page.evaluate(([u]) => {
    window.__dm.caption('');
    window.__dm.end([['h1', 'Early Warning'], ['p', 'Graph-aware early warning for non-performing loans'],
                     ['p', '<b>Gemini</b> · <b>Cloud Run</b> · <b>Cloud Build</b>'], ['p', u], ['p', 'github.com/Hu2Hoang/EWS']]);
  }, [BASE.replace(/^https?:\/\//, '')]);
});
await sleep(2);
const end = now();

await cdp.send('Page.stopScreencast');
await sleep(0.5);
fs.writeFileSync(path.join(OUT, 'timeline.json'), JSON.stringify({ frames, marks, end }, null, 0));
console.log(`frames: ${frames.length}, length ≈ ${(end - marks[0].t).toFixed(1)}s, first frame ts ${frames[0]?.ts} vs first mark ${marks[0].t}`);
if (errors.length) console.log('page errors:', errors);
await browser.close();
