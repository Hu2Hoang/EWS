// Captures the prototype screenshots for the deck: node snapshots.mjs [APP_URL]  ->  docs/snapshots/*.png
import path from 'node:path';
import { chromium } from 'playwright-core';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const OUT = path.join(HERE, '..', 'docs', 'snapshots');
const BASE = (process.argv[2] ?? 'http://localhost:4200').replace(/\/$/, '');
const API = process.env.API ?? (new URL(BASE).hostname === 'localhost' ? 'http://localhost:8000' : `${BASE}/api`);
const sleep = (s) => new Promise((ok) => setTimeout(ok, s * 1000));

const browser = await chromium.launch({ executablePath: process.env.CHROME ?? '/usr/bin/google-chrome', headless: true,
                                        args: ['--no-sandbox', '--hide-scrollbars', '--window-size=1440,987', '--force-device-scale-factor=1.3333333'] });
const page = await (await browser.newContext({ viewport: null })).newPage();
const shot = async (name) => { await sleep(0.6); await page.screenshot({ path: path.join(OUT, `${name}.png`) }); console.log('📸', name); };
const tab = (name) => page.locator('nav.tabs button', { hasText: name }).click();
const top = () => page.evaluate(() => window.scrollTo({ top: 0 }));

await fetch(`${API}/live/reset`, { method: 'POST' });
await page.goto(`${BASE}/?v=overview&c=C_A&m=2026-05`);
await page.waitForSelector('.tiles', { timeout: 60000 });
await sleep(2);
await shot('01-command-center');

await tab('Case review');
await page.waitForSelector('.panel .ai, .panel .btn.run', { timeout: 30000 });
const run = page.getByRole('button', { name: 'Run AI analysis' });
if (await run.isVisible().catch(() => false)) await run.click();
await page.waitForSelector('.panel .ai', { timeout: 60000 });
await page.locator('.panel .sig', { hasText: 'Related company' }).click();
await sleep(1.5);
await shot('02-case-review-gemini');

await page.locator('.graph .sim').click();
await page.waitForSelector('.lab-tiles');
await page.locator('#lab-month').selectOption('2026-09-01');
await sleep(1);
await page.getByRole('button', { name: 'Largest healthy counterparty defaults' }).click();
await sleep(5);
await page.evaluate(() => window.scrollTo({ top: document.querySelector('.lab-tiles').getBoundingClientRect().top + scrollY - 96 }));
await shot('03-contagion-lab');

await page.evaluate(() => document.querySelector('.mx')?.scrollIntoView({ block: 'start' }));
const cells = page.locator('.mx-t tbody tr').nth(1).locator('.cell');
await cells.nth(4).click();
await sleep(1.5);
await page.evaluate(() => window.scrollBy(0, -60));
await shot('04-contagion-matrix');

await top(); await tab('News to graph');
await page.locator('.samples .preset', { hasText: 'Vietnamese' }).click();
await page.waitForSelector('.news-ev', { timeout: 60000 });
await shot('05-news-to-graph');
await page.getByRole('button', { name: 'Apply to graph' }).first().click();
await page.waitForSelector('.ok-note');

await top(); await tab('Case review');
await page.waitForSelector('.add-task');
for (const a of ['request_additional_collateral', 'review_related_exposure', 'increase_monitoring']) {
  await page.locator('#new-action').selectOption(a);
  await page.getByRole('button', { name: 'Add task' }).click();
  await sleep(1.2);
}
await top(); await tab('Action board');
await page.waitForSelector('.kanban .task');
await page.locator('.task', { hasText: 'Request Additional Collateral' }).getByRole('button', { name: 'Complete' }).click();
await page.locator('.col').nth(2).locator('.result').first().waitFor();
await page.locator('.task', { hasText: 'Ring-fence' }).getByRole('button', { name: 'Complete' }).click();
await sleep(2);
await page.locator('.task', { hasText: 'Increase Monitoring' }).getByRole('button', { name: 'Start' }).click();
await sleep(1.5);
await page.evaluate(() => window.scrollTo({ top: 330 }));
await shot('06-action-board');

await top(); await tab('Case review');
await page.waitForSelector('.panel .sig');
await sleep(2);
await page.locator('.fab').click();
await page.locator('.sugg', { hasText: 'shares a related company' }).click();
await page.waitForFunction(() => document.querySelectorAll('.msg.model').length && !document.querySelector('.msgs .muted')?.textContent?.includes('Thinking'), null, { timeout: 60000 });
await shot('07-ask-gemini');
await page.locator('.chat .x').click();

await top(); await tab('Stress test');
await page.getByRole('button', { name: 'Property market slump' }).click();
await sleep(3);
await shot('08-stress-test');

await fetch(`${API}/live/reset`, { method: 'POST' });
await browser.close();
