/* ===================================================================
   Megatron — Client-side JS
   =================================================================== */

// -------------------------------------------------------------------
// DOM refs
// -------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const messagesEl    = $('#messages');
const promptForm    = $('#prompt-form');
const promptInput   = $('#prompt-input');
const sendBtn       = $('#send-btn');
const typingEl      = $('#typing-indicator');
const connectionDot = $('#connection-dot');
const modelSelect   = $('#model-select');
const clockEl       = $('#clock');
const clearBtn      = $('#clear-btn');
const powerBtn      = $('#power-btn');
const powerMenu     = $('#power-menu');
const screenshotPanel = $('#screenshot-panel');
const screenshotImg   = $('#screenshot-img');
const closeScreenshot = $('#close-screenshot');
const fragmentPanel   = $('#fragment-panel');
const fragmentImg     = $('#fragment-img');
const fragmentLabel   = $('#fragment-label');
const closeFragment   = $('#close-fragment');
const shortcutBtns    = $$('.shortcut');

// Media player refs
const mediaPlayer   = $('#media-player');
const mpPrev        = $('#mp-prev');
const mpPlay        = $('#mp-play');
const mpNext        = $('#mp-next');
const mpTitle       = $('#mp-title');
const mpMute        = $('#mp-mute');
const mpVolume      = $('#mp-volume');
const mpVolLabel    = $('#mp-vol-label');

// Confirm dialog refs
const confirmOverlay = $('#confirm-overlay');
const confirmText    = $('#confirm-text');
const confirmOk      = $('#confirm-ok');
const confirmCancel  = $('#confirm-cancel');
let confirmAction    = null;

// Configure marked
marked.setOptions({
  breaks: true,
  gfm: true,
});

// -------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------
let toastTimer = null;
function showToast(msg) {
  let el = $('#status-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'status-toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), 2500);
}

function showConfirm(text, callback) {
  confirmText.textContent = text;
  confirmAction = callback;
  confirmOverlay.classList.remove('hidden');
}

function hideConfirm() {
  confirmOverlay.classList.add('hidden');
  confirmAction = null;
}

confirmCancel.addEventListener('click', hideConfirm);
confirmOk.addEventListener('click', () => {
  if (confirmAction) confirmAction();
  hideConfirm();
});

confirmOverlay.addEventListener('click', (e) => {
  if (e.target === confirmOverlay) hideConfirm();
});

// -------------------------------------------------------------------
// SocketIO
// -------------------------------------------------------------------
const socket = io({
  transports: ['polling', 'websocket'],
  reconnection: true,
  reconnectionDelay: 1000,
  reconnectionDelayMax: 5000,
  maxHttpBufferSize: 5e6,
});

socket.on('connect', () => {
  connectionDot.className = 'dot connected';
  showToast('Connected');
  checkMediaStatus();
});

socket.on('disconnect', () => {
  connectionDot.className = 'dot disconnected';
  showToast('Disconnected');
});

socket.on('server_status', (data) => {
  if (data.model) {
    const opt = modelSelect.querySelector(`option[value="${data.model}"]`);
    if (!opt) {
      const o = document.createElement('option');
      o.value = data.model;
      o.textContent = '🔹 ' + data.model.split('/').pop();
      o.selected = true;
      modelSelect.appendChild(o);
    } else {
      modelSelect.value = data.model;
    }
  }
});

socket.on('status', (data) => showToast(data.message));

socket.on('tool_start', (data) => {
  addMessage(`🔧 Running: ${data.tool}`, 'tool');
});

socket.on('tool_result', (data) => {
  const res = data.result;
  if (res && res.ok === false) {
    addMessage(`⚠️ ${data.tool} failed: ${res.error}`, 'error');
  } else if (res && res.stdout !== undefined) {
    addMessage(`💻 ${data.tool}\n\`\`\`\n${res.stdout}\n\`\`\``, 'tool');
  } else if (res && res.message) {
    addMessage(`✅ ${res.message}`, 'tool');
  }
  // Refresh media bar after any VLC action
  checkMediaStatus();
});

socket.on('screenshot', (data) => {
  console.log('SCREENSHOT received', data.image ? data.image.length : 0, 'bytes');
  screenshotPanel.classList.remove('hidden');
  const img = new Image();
  img.onload = () => {
    screenshotImg.src = img.src;
    $('#main').scrollTo({ top: screenshotPanel.offsetTop - 20, behavior: 'smooth' });
  };
  img.src = `data:image/jpeg;base64,${data.image}`;
});

socket.on('screen_fragment', (data) => {
  console.log('FRAGMENT received', data.target, data.image ? data.image.length : 0, 'bytes');
  fragmentLabel.textContent = `Found: "${data.target}" at (${data.bbox.x}, ${data.bbox.y})`;
  fragmentPanel.classList.remove('hidden');
  const img = new Image();
  img.onload = () => {
    fragmentImg.src = img.src;
    $('#main').scrollTo({ top: fragmentPanel.offsetTop - 20, behavior: 'smooth' });
  };
  img.src = `data:image/jpeg;base64,${data.image}`;
});

socket.on('image_results', (data) => {
  // Show search images as a grid in a message
  if (data.images && data.images.length > 0) {
    const html = data.images.map(im =>
      `<div class="img-result"><img src="${im.image_url}" alt="${im.title}" loading="lazy"><p>${im.title}</p></div>`
    ).join('');
    addMessage(`<div class="img-grid">${html}</div>`, 'assistant');
  }
});

socket.on('response', (data) => {
  setTyping(false);
  if (data.text) {
    addMessage(data.text, 'assistant');
    scrollToBottom();
  }
});

socket.on('error', (data) => {
  setTyping(false);
  addMessage(`❌ ${data.message}`, 'error');
  console.error('Server error:', data);
});

socket.on('history_cleared', () => {
  clearAllMessages();
  showToast('History cleared');
});

// -------------------------------------------------------------------
// Clear button
// -------------------------------------------------------------------
clearBtn.addEventListener('click', () => {
  socket.emit('clear_history');
});

function clearAllMessages() {
  messagesEl.innerHTML = `
    <div class="welcome">
      <div class="welcome-icon">🖥️</div>
      <h2>Ready to control your PC</h2>
      <p>Type a prompt or tap a shortcut below</p>
    </div>`;
  screenshotPanel.classList.add('hidden');
  fragmentPanel.classList.add('hidden');
}

// -------------------------------------------------------------------
// Close buttons
// -------------------------------------------------------------------
closeScreenshot.addEventListener('click', () => screenshotPanel.classList.add('hidden'));
closeFragment.addEventListener('click', () => fragmentPanel.classList.add('hidden'));

// -------------------------------------------------------------------
// Message rendering (with Markdown support)
// -------------------------------------------------------------------
function addMessage(text, role) {
  const welcome = messagesEl.querySelector('.welcome');
  if (welcome) welcome.remove();

  const div = document.createElement('div');
  div.className = `message ${role}`;

  if (role === 'assistant') {
    // Render markdown for assistant messages
    div.innerHTML = marked.parse(text);
  } else if (role === 'tool' && text.includes('```')) {
    const parts = text.split('```');
    parts.forEach((part, i) => {
      if (i % 2 === 1) {
        const pre = document.createElement('pre');
        pre.textContent = part;
        div.appendChild(pre);
      } else if (part) {
        div.appendChild(document.createTextNode(part));
      }
    });
  } else {
    div.textContent = text;
  }

  messagesEl.appendChild(div);
  scrollToBottom();
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    $('#main').scrollTop = $('#main').scrollHeight;
  });
}

// -------------------------------------------------------------------
// Typing indicator
// -------------------------------------------------------------------
function setTyping(active) {
  if (active) {
    typingEl.classList.remove('hidden');
    sendBtn.disabled = true;
    promptInput.disabled = true;
  } else {
    typingEl.classList.add('hidden');
    sendBtn.disabled = false;
    promptInput.disabled = false;
    promptInput.focus();
  }
}

// -------------------------------------------------------------------
// Prompt submission
// -------------------------------------------------------------------
function submitPrompt(text) {
  text = text.trim();
  if (!text || sendBtn.disabled) return;

  screenshotPanel.classList.add('hidden');
  fragmentPanel.classList.add('hidden');

  addMessage(text, 'user');
  setTyping(true);
  socket.emit('prompt', { text });
  promptInput.value = '';
  scrollToBottom();
}

promptForm.addEventListener('submit', (e) => {
  e.preventDefault();
  submitPrompt(promptInput.value);
});

// -------------------------------------------------------------------
// Shortcut buttons
// -------------------------------------------------------------------
shortcutBtns.forEach((btn) => {
  btn.addEventListener('click', () => {
    const action = btn.dataset.action;
    const prompt = btn.dataset.prompt;

    if (action === 'esc') {
      // Direct escape key bypass — no model involved
      fetch('/api/input/press', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'esc' }),
      }).then(r => r.json()).then(() => showToast('Esc sent')).catch(e => showToast('Esc failed'));
      return;
    }

    if (prompt) submitPrompt(prompt);
  });
});

// -------------------------------------------------------------------
// Power menu
// -------------------------------------------------------------------
powerBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  powerMenu.classList.toggle('hidden');
});

document.addEventListener('click', (e) => {
  if (!powerMenu.contains(e.target) && e.target !== powerBtn) {
    powerMenu.classList.add('hidden');
  }
});

$$('.power-item').forEach((btn) => {
  btn.addEventListener('click', () => {
    const action = btn.dataset.action;
    powerMenu.classList.add('hidden');

    if (action === 'shutdown') {
      showConfirm('Shutdown the PC?', () => doPowerAction('shutdown'));
    } else if (action === 'hibernate') {
      showConfirm('Hibernate the PC?', () => doPowerAction('hibernate'));
    } else if (action === 'reboot') {
      showConfirm('Reboot the PC?', () => doPowerAction('reboot'));
    } else {
      doPowerAction(action);
    }
  });
});

async function doPowerAction(action) {
  try {
    const resp = await fetch('/api/system/power', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    const data = await resp.json();
    showToast(data.ok ? `${action}…` : `Failed: ${data.error}`);
  } catch (e) {
    showToast(`Power ${action} failed`);
  }
}

// -------------------------------------------------------------------
// Media player controls (bypass model)
// -------------------------------------------------------------------
async function checkMediaStatus() {
  try {
    const resp = await fetch('/api/media/status');
    const data = await resp.json();
    const vol = data.volume ?? 50;
    mpVolume.value = vol;
    mpVolLabel.textContent = vol;
    mpMute.textContent = data.muted ? '🔈' : '🔊';
    if (data.now_playing) {
      mpTitle.textContent = data.now_playing;
    }
    mediaPlayer.classList.remove('hidden');
  } catch (e) {
    // Can't read volume — hide the bar
    mediaPlayer.classList.add('hidden');
  }
}

// Auto-sync volume every 3 seconds
setInterval(checkMediaStatus, 3000);

mpPlay.addEventListener('click', async () => {
  try {
    await fetch('/api/media/playpause', { method: 'POST' });
    checkMediaStatus();
  } catch (e) { showToast('Play/pause failed'); }
});

mpPrev.addEventListener('click', async () => {
  try {
    await fetch('/api/media/prev', { method: 'POST' });
    checkMediaStatus();
  } catch (e) { showToast('Previous failed'); }
});

mpNext.addEventListener('click', async () => {
  try {
    await fetch('/api/media/next', { method: 'POST' });
    checkMediaStatus();
  } catch (e) { showToast('Next failed'); }
});

mpMute.addEventListener('click', async () => {
  try {
    const resp = await fetch('/api/media/mute', { method: 'POST' });
    const data = await resp.json();
    mpMute.textContent = data.muted ? '🔈' : '🔊';
  } catch (e) { showToast('Mute failed'); }
});

mpVolume.addEventListener('input', async () => {
  const vol = mpVolume.value;
  mpVolLabel.textContent = vol;
  try {
    await fetch('/api/media/volume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level: parseInt(vol) }),
    });
  } catch (e) { /* silent */ }
});

// -------------------------------------------------------------------
// Clock
// -------------------------------------------------------------------
function updateClock() {
  const now = new Date();
  clockEl.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
updateClock();
setInterval(updateClock, 10000);

// -------------------------------------------------------------------
// Model selection — actually switch models via API
// -------------------------------------------------------------------
async function loadModels() {
  try {
    const resp = await fetch('/api/models');
    const data = await resp.json();
    if (data.ok && data.models) {
      const current = modelSelect.value;
      modelSelect.innerHTML = '<option value="">Select model…</option>';
      data.models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = (m.has_vision ? '👁️ ' : '') + m.name;
        opt.title = `${m.id}\n${m.size_mb} MB${m.has_vision ? ' · vision' : ''}`;
        if (current && m.id === current) opt.selected = true;
        modelSelect.appendChild(opt);
      });
    }
  } catch (e) {
    console.warn('Failed to load models:', e);
  }
}

modelSelect.addEventListener('change', async () => {
  const selected = modelSelect.value;
  if (!selected) return;

  showToast(`Loading: ${selected.split('/').pop()}…`);
  try {
    const resp = await fetch('/api/model/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: selected }),
    });
    const data = await resp.json();
    if (data.ok) {
      showToast(`Loaded: ${selected.split('/').pop()}`);
      connectionDot.className = 'dot connected';
    } else {
      showToast(`Switch failed: ${data.error}`);
    }
  } catch (e) {
    showToast('Model switch failed');
  }
});

loadModels();

// -------------------------------------------------------------------
// Focus input
// -------------------------------------------------------------------
promptInput.focus();

$('#main').addEventListener('click', (e) => {
  if (!e.target.closest('.panel') && !e.target.closest('.message')) {
    promptInput.focus();
  }
});
