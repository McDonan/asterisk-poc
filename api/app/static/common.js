/* Shared helpers of the demo pages (plain JS, no build step). */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));

let CFG = null;
async function loadConfig() {
  if (!CFG) CFG = await (await fetch('/demo/config')).json();
  return CFG;
}

async function api(path, method = 'GET', body = null, quiet = false) {
  const cfg = await loadConfig();
  const r = await fetch(path, {
    method, headers: { 'X-API-Key': cfg.api_key, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) {
    const msg = (data && (data.detail || data.message)) || r.statusText;
    if (!quiet) toast(typeof msg === 'string' ? msg : JSON.stringify(msg), 'err');
    throw new Error(msg);
  }
  return data;
}

function toast(msg, kind = '') {
  let box = $('.toast');
  if (!box) { box = document.createElement('div'); box.className = 'toast'; document.body.appendChild(box); }
  const d = document.createElement('div');
  d.className = kind; d.textContent = msg; box.appendChild(d);
  setTimeout(() => d.remove(), 4500);
}

/** WebSocket of real-time AMI events (auto-reconnect). */
async function watchEvents(onEvent) {
  const cfg = await loadConfig();
  const open = () => {
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/events?api_key=${cfg.api_key}`);
    ws.onmessage = m => { try { onEvent(JSON.parse(m.data)); } catch (e) { /* ignore */ } };
    ws.onclose = () => setTimeout(open, 2000);
  };
  open();
}

/** Plays the IVR prompt wav files in the browser (simulated customers have no real audio path). */
class IvrPlayer {
  constructor() { this.a = null; this.enabled = true; }
  play(prompt) {
    if (!this.enabled) return;
    this.stop();
    this.a = new Audio(`/static/ivr/${prompt}.wav`);
    this.a.play().catch(() => {});
  }
  stop() { if (this.a) { this.a.pause(); this.a = null; } }
}

function nav(cur) {
  const links = [['/demo', 'หน้าแรก'], ['/demo/customer', 'ลูกค้า'], ['/demo/agent', 'Agent'], ['/demo/dashboard', 'Dashboard'],
    ['/demo/transactions', 'Transactions'], ['/demo/flow', 'Flow Map']];
  return `<nav>${links.map(([h, t]) => `<a href="${h}" ${h === cur ? 'class="cur"' : ''}>${t}</a>`).join('')}</nav>`;
}

const fmtTime = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString('th-TH', { hour12: false }); };
const fmtDateTime = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString('th-TH', { hour12: false }); };
const mmss = s => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
