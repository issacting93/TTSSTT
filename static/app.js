/* ───────────── TTSSTT — Frontend ───────────── */

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const audio = new Audio();
let audioCtx, analyser, srcNode;
let waveAnim;

const state = {
  view: 'view-home',
  library: [],
  zotero: [],
  track: null,
  playing: false,
  jobId: null,
  voice: localStorage.getItem('ttsstt-voice') || 'af_heart',
};

/* ── Init ── */
document.addEventListener('DOMContentLoaded', () => {
  updateClock();
  setInterval(updateClock, 30000);
  loadLibrary();
  loadVoices();
  loadZotero();
  bind();
});

/* ── Event bindings ── */
function bind() {
  $('#btn-convert').onclick = startConvert;
  $('#url-input').onkeydown = (e) => { if (e.key === 'Enter') startConvert(); };

  $$('.nav-btn').forEach(b => b.onclick = () => switchView(b.dataset.view));
  $('#btn-back').onclick = () => switchView('view-home');
  $('#btn-back-set').onclick = () => switchView('view-home');

  $('#btn-play').onclick = togglePlay;
  $('#btn-rwd').onclick = () => seek(-10);
  $('#btn-fwd').onclick = () => seek(10);

  $('#progress-bar').oninput = (e) => {
    if (audio.duration) audio.currentTime = (e.target.value / 1000) * audio.duration;
  };

  audio.ontimeupdate = onTimeUpdate;
  audio.onended = () => { state.playing = false; updatePlayUI(); stopReel(); };
  audio.onplay = () => { state.playing = true; updatePlayUI(); startReel(); drawWave(); };
  audio.onpause = () => { state.playing = false; updatePlayUI(); stopReel(); };

  $('#voice-select').onchange = (e) => {
    state.voice = e.target.value;
    localStorage.setItem('ttsstt-voice', e.target.value);
    $('#info-voice').textContent = e.target.selectedOptions[0]?.text || e.target.value;
  };

  $('#zotero-search').oninput = () => renderZotero();
}

/* ── Clock ── */
function updateClock() {
  const now = new Date();
  const days = ['SUNDAY','MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY'];
  $('#day-name').textContent = days[now.getDay()];
  const p = (n) => String(n).padStart(2, '0');
  $('#date-line').textContent =
    `${p(now.getMonth()+1)}.${p(now.getDate())}.${String(now.getFullYear()).slice(-2)} ${p(now.getHours())}.${p(now.getMinutes())}`;
}

/* ── Views ── */
function switchView(id) {
  state.view = id;
  $$('.view').forEach(v => v.classList.remove('active'));
  $(`#${id}`).classList.add('active');
  $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.view === id));
}

/* ── Library ── */
async function loadLibrary() {
  try {
    const r = await fetch('/api/library');
    state.library = await r.json();
    renderLibrary();
  } catch(e) { console.error(e); }
}

function renderLibrary() {
  const el = $('#track-list');
  if (!state.library.length) {
    el.innerHTML = '<div class="empty-state">No recordings yet.<br>Paste a PDF URL to begin.</div>';
    updateStats();
    return;
  }
  el.innerHTML = state.library.map(t => `
    <div class="track-item" data-id="${t.id}">
      <span class="track-marker">&#9658;</span>
      <span class="track-name">${esc(t.title)}</span>
      <span class="track-dur">${fmtDur(t.duration_seconds)}</span>
      <button class="track-del" data-id="${t.id}" title="Delete">&times;</button>
    </div>
  `).join('');

  el.querySelectorAll('.track-item').forEach(item => {
    item.onclick = (e) => {
      if (e.target.classList.contains('track-del')) {
        e.stopPropagation();
        delTrack(e.target.dataset.id);
        return;
      }
      const t = state.library.find(x => x.id === item.dataset.id);
      if (t) playTrack(t);
    };
  });

  updateStats();
}

function updateStats() {
  const total = state.library.reduce((s, t) => s + (t.duration_seconds || 0), 0);
  $('#info-stats').textContent = `${state.library.length} file${state.library.length !== 1 ? 's' : ''} / ${fmtDur(total)}`;
}

/* ── Conversion ── */
async function startConvert() {
  const url = $('#url-input').value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) { alert('Enter a valid URL'); return; }
  runConvert({ url });
}

async function runConvert(payload, statusBtn) {
  const btn = statusBtn || $('#btn-convert');
  btn.disabled = true;
  btn.classList.add('converting');
  btn.textContent = payload.local_path ? 'READING...' : 'DOWNLOADING...';

  try {
    const r = await fetch('/api/convert', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ ...payload, voice: state.voice }),
    });
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    state.jobId = data.job_id;
    pollJob(data.job_id, btn);
  } catch(e) {
    btn.disabled = false;
    btn.classList.remove('converting');
    btn.textContent = statusBtn ? 'REC' : 'CONVERT';
    alert(e.message);
  }
}

async function pollJob(id, statusBtn) {
  const btn = statusBtn || $('#btn-convert');
  const defaultLabel = statusBtn ? 'REC' : 'CONVERT';
  const labels = {
    reading: 'READING...',
    downloading: 'DOWNLOADING...',
    extracting: 'EXTRACTING...',
    synthesizing: 'SYNTHESIZING...',
    encoding: 'ENCODING...',
    complete: 'DONE',
    error: 'ERROR',
  };

  const tick = async () => {
    try {
      const r = await fetch(`/api/status/${id}`);
      const d = await r.json();
      btn.textContent = labels[d.status] || d.status.toUpperCase();

      if (['synthesizing', 'encoding', 'downloading'].includes(d.status)) bounceMeter();

      if (d.status === 'complete') {
        btn.disabled = false;
        btn.classList.remove('converting');
        btn.textContent = defaultLabel;
        if ($('#url-input')) $('#url-input').value = '';
        state.jobId = null;
        stopMeter();
        await loadLibrary();
        return;
      }
      if (d.status === 'error') {
        btn.disabled = false;
        btn.classList.remove('converting');
        btn.textContent = defaultLabel;
        state.jobId = null;
        stopMeter();
        alert('Failed: ' + (d.error || 'Unknown'));
        return;
      }
      setTimeout(tick, 800);
    } catch(e) {
      btn.disabled = false;
      btn.classList.remove('converting');
      btn.textContent = defaultLabel;
      stopMeter();
    }
  };
  tick();
}

/* ── Meter bounce ── */
let meterTimer;
function bounceMeter() {
  if (meterTimer) return;
  meterTimer = setInterval(() => {
    const l = 15 + Math.random() * 55;
    const r = 15 + Math.random() * 55;
    $('#meter-l').style.width = l + '%';
    $('#meter-r').style.width = r + '%';
  }, 120);
}
function stopMeter() {
  clearInterval(meterTimer);
  meterTimer = null;
  $('#meter-l').style.width = '0%';
  $('#meter-r').style.width = '0%';
}

/* ── Playback ── */
function playTrack(t) {
  state.track = t;
  audio.src = `/api/audio/${t.filename}`;
  audio.play().catch(() => {});
  initAudio();
  switchView('view-player');
  $('#play-title').textContent = t.title;
  $('#play-badge').textContent = 'PLAYING';
  $('#timer').textContent = '0h00m00s';
}

function initAudio() {
  if (audioCtx) return;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  srcNode = audioCtx.createMediaElementSource(audio);
  srcNode.connect(analyser);
  analyser.connect(audioCtx.destination);
}

function togglePlay() {
  if (!state.track) return;
  if (state.playing) audio.pause();
  else { audio.play().catch(() => {}); }
}

function seek(s) {
  if (!audio.duration) return;
  audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + s));
}

function updatePlayUI() {
  $('#play-icon').innerHTML = state.playing ? '&#9646;&#9646;' : '&#9654;';
  $('#play-badge').textContent = state.playing ? 'PLAYING' : 'PAUSED';
}

function onTimeUpdate() {
  if (!audio.duration) return;
  const pct = (audio.currentTime / audio.duration) * 1000;
  $('#progress-bar').value = pct;
  $('#time-current').textContent = fmtTime(audio.currentTime);
  $('#time-total').textContent = fmtTime(audio.duration);
  $('#timer').textContent = fmtDurLong(audio.currentTime);

  // playhead
  const cw = $('.wave-container');
  if (cw) {
    const x = (audio.currentTime / audio.duration) * cw.offsetWidth;
    $('#playhead').style.left = x + 'px';
  }
}

/* ── Reel ── */
function startReel() { $('#reel').classList.add('spinning'); }
function stopReel()  { $('#reel').classList.remove('spinning'); }

/* ── Waveform ── */
function drawWave() {
  if (!analyser) return;
  const canvas = $('#waveform');
  const ctx = canvas.getContext('2d');
  const bufLen = analyser.frequencyBinCount;
  const data = new Uint8Array(bufLen);

  const paint = () => {
    if (!state.playing) {
      cancelAnimationFrame(waveAnim);
      return;
    }
    waveAnim = requestAnimationFrame(paint);
    analyser.getByteTimeDomainData(data);

    const w = canvas.width, h = canvas.height;
    ctx.fillStyle = '#dbd7d1';
    ctx.fillRect(0, 0, w, h);

    // Grid lines
    ctx.strokeStyle = '#c8c4be';
    ctx.lineWidth = 0.5;
    for (let y = h * 0.25; y < h; y += h * 0.25) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    for (let x = 0; x < w; x += w / 16) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }

    // Waveform
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = '#1a1a1a';
    ctx.beginPath();
    const step = w / bufLen;
    for (let i = 0; i < bufLen; i++) {
      const v = data[i] / 128.0;
      const y = (v * h) / 2;
      if (i === 0) ctx.moveTo(0, y);
      else ctx.lineTo(i * step, y);
    }
    ctx.stroke();

    // Update meters from frequency data
    analyser.getByteFrequencyData(data);
    const avg = data.reduce((a, b) => a + b, 0) / bufLen;
    const lvl = Math.min(100, (avg / 255) * 160);
    $('#meter-l').style.width = lvl + '%';
    $('#meter-r').style.width = (lvl * 0.85 + Math.random() * 8) + '%';
  };
  paint();
}

/* ── Delete ── */
async function delTrack(id) {
  if (!confirm('Delete this recording?')) return;
  await fetch(`/api/track/${id}`, { method: 'DELETE' });
  if (state.track && state.track.id === id) {
    audio.pause();
    audio.src = '';
    state.track = null;
    state.playing = false;
    switchView('view-home');
  }
  await loadLibrary();
}

/* ── Voices ── */
async function loadVoices() {
  try {
    const r = await fetch('/api/voices');
    const voices = await r.json();
    const sel = $('#voice-select');
    sel.innerHTML = voices.map(v =>
      `<option value="${v.id}" ${v.id === state.voice ? 'selected' : ''}>${v.name}</option>`
    ).join('');
    const cur = voices.find(v => v.id === state.voice);
    $('#info-voice').textContent = cur ? cur.name : state.voice;
  } catch(e) { console.error(e); }
}

/* ── Helpers ── */
function fmtDur(sec) {
  if (!sec) return '0m';
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}h${String(m).padStart(2,'0')}m${String(s).padStart(2,'0')}s`;
  return `${m}m${String(s).padStart(2,'0')}s`;
}

function fmtDurLong(sec) {
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${h}h${String(m).padStart(2,'0')}m${String(s).padStart(2,'0')}s`;
}

function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(0) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

/* ── Zotero ── */
async function loadZotero() {
  try {
    const r = await fetch('/api/zotero');
    state.zotero = await r.json();
    const el = $('#zotero-count');
    if (el) el.textContent = `${state.zotero.length} papers`;
    renderZotero();
  } catch(e) { console.error(e); }
}

function renderZotero() {
  const el = $('#zotero-list');
  if (!el) return;

  const q = ($('#zotero-search')?.value || '').toLowerCase();
  const filtered = q
    ? state.zotero.filter(p => p.name.toLowerCase().includes(q))
    : state.zotero;

  if (!filtered.length) {
    el.innerHTML = `<div class="zotero-empty">${q ? 'No matches.' : 'No papers found.'}</div>`;
    return;
  }

  el.innerHTML = filtered.map((p, i) => `
    <div class="zotero-item">
      <span class="zotero-name" title="${esc(p.name)}">${esc(p.name)}</span>
      <span class="zotero-size">${fmtSize(p.size)}</span>
      <button class="zotero-go" data-idx="${i}">REC</button>
    </div>
  `).join('');

  el.querySelectorAll('.zotero-go').forEach(btn => {
    btn.onclick = () => {
      const paper = filtered[parseInt(btn.dataset.idx)];
      if (paper) runConvert({ local_path: paper.path }, btn);
    };
  });
}
