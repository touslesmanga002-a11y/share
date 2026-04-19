"""
Proxy navigateur léger — Render.com
OPTIMISÉ : frames JPEG en push binaire WebSocket (pas de MJPEG HTTP)
"""

import os
import threading
import time
import json
from flask import Flask, Response, request, render_template_string
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

# ─────────────────────────────────────────────
#  État global
# ─────────────────────────────────────────────
_playwright  = None
_browser     = None
_page        = None
_page_lock   = threading.Lock()

_last_shot   = None          # bytes JPEG
_shot_lock   = threading.Lock()
_shot_event  = threading.Event()   # ← notifie les clients dès qu'une frame est prête

# Paramètres dynamiques
_quality     = 35            # % JPEG (bas = léger)
_sleep       = 0.10          # secondes entre captures (≈10 fps)
_params_lock = threading.Lock()


# ─────────────────────────────────────────────
#  PAGE D'ACCUEIL
# ─────────────────────────────────────────────
LANDING_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Proxy navigateur léger</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, sans-serif;
      background: #0f0f1a; color: #e0e0e0;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      height: 100vh; gap: 1.5rem;
    }
    h1 { font-size: 1.8rem; }
    p  { font-size: .9rem; color: #888; }
    form { display: flex; gap: .5rem; width: min(500px, 92vw); }
    input {
      flex: 1; padding: .65rem 1rem;
      border: 1px solid #333; border-radius: 6px;
      background: #1c1c2e; color: #fff; font-size: 1rem;
    }
    input:focus { outline: 2px solid #e94560; }
    button {
      padding: .65rem 1.4rem; background: #e94560;
      border: none; border-radius: 6px;
      color: #fff; font-size: 1rem; cursor: pointer;
    }
    button:hover { background: #c73652; }
    .hint { font-size: .78rem; color: #555; }
  </style>
</head>
<body>
  <h1>🌐 Proxy navigateur léger</h1>
  <p>Entrez une URL pour la consulter via ce proxy.</p>
  <form onsubmit="navigate(event)">
    <input id="u" type="text" placeholder="exemple.com" autofocus>
    <button type="submit">Aller</button>
  </form>
  <span class="hint">Astuce : vous pouvez aussi écrire <code>/?exemple.com</code> dans la barre d'adresse.</span>
  <script>
    function navigate(e) {
      e.preventDefault();
      const val = document.getElementById('u').value.trim();
      if (val) window.location.href = '/?' + encodeURIComponent(val);
    }
  </script>
</body>
</html>"""


# ─────────────────────────────────────────────
#  PAGE VIEWER
# ─────────────────────────────────────────────
VIEWER_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ url }}</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #000; display: flex;
      height: 100vh; overflow: hidden;
      font-family: system-ui, sans-serif;
    }

    /* ═══ SIDEBAR ═══ */
    #sidebar {
      width: 18px; min-height: 100%;
      background: #1a1a2e; border-right: 1px solid #2a2a4a;
      transition: width .25s ease;
      overflow: hidden; flex-shrink: 0;
      display: flex; flex-direction: column;
      z-index: 50; position: relative;
    }
    #sidebar.open { width: 230px; }

    #sidebar-toggle {
      position: absolute; right: 0; top: 50%;
      transform: translateY(-50%);
      width: 18px; height: 56px;
      background: #e94560; border-radius: 0 6px 6px 0;
      cursor: pointer; display: flex;
      align-items: center; justify-content: center;
      color: #fff; font-size: 11px;
      writing-mode: vertical-rl;
      user-select: none; z-index: 2;
      transition: right .25s;
    }
    #sidebar.open #sidebar-toggle { right: -18px; }

    #sidebar-content {
      padding: 12px 10px;
      display: flex; flex-direction: column; gap: 18px;
      overflow-y: auto; overflow-x: hidden;
      flex: 1; margin-top: 8px;
      opacity: 0; transition: opacity .2s .1s;
      min-width: 210px;
    }
    #sidebar.open #sidebar-content { opacity: 1; }

    .panel-title {
      font-size: .65rem; text-transform: uppercase;
      letter-spacing: .1em; color: #666; margin-bottom: 2px;
    }
    .panel-section { display: flex; flex-direction: column; gap: 6px; }

    .slider-row { display: flex; align-items: center; gap: 8px; }
    .slider-row input[type=range] { flex: 1; accent-color: #e94560; cursor: pointer; }
    .slider-val { font-size: .75rem; color: #aaa; width: 36px; text-align: right; }

    .side-btn {
      width: 100%; padding: 8px 10px;
      background: #2c2c4a; border: 1px solid #3a3a6a;
      border-radius: 8px; color: #ddd;
      font-size: .82rem; cursor: pointer; text-align: left;
    }
    .side-btn:hover  { background: #3a3a6a; }
    .side-btn.active { background: #e94560; border-color: #e94560; color: #fff; }

    /* ═══ ZONE PRINCIPALE ═══ */
    #main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

    #bar {
      display: flex; align-items: center; gap: 8px;
      background: #1a1a2e; color: #ccc;
      padding: 4px 10px; font-size: .8rem; flex-shrink: 0;
    }
    #bar a { color: #7eb8f7; text-decoration: none; }
    #bar a:hover { text-decoration: underline; }
    #url-display { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    #fps-label   { color: #6f6; font-size: .75rem; white-space: nowrap; }
    #ping-label  { color: #7eb8f7; font-size: .75rem; white-space: nowrap; }
    #status      { color: #fa0; font-size: .75rem; white-space: nowrap; }

    #viewport {
      flex: 1; position: relative; overflow: hidden;
      display: flex; align-items: flex-start; justify-content: center;
      cursor: none;
    }
    #stream {
      max-width: 100%; max-height: 100%;
      display: block; object-fit: contain;
      image-rendering: optimizeSpeed;
    }

    #cursor {
      position: fixed; width: 14px; height: 14px;
      border-radius: 50%; background: rgba(233,69,96,.85);
      border: 2px solid #fff; pointer-events: none;
      transform: translate(-50%,-50%); z-index: 100;
      transition: background .08s;
    }
    #cursor.clicking { background: #fff; }

    /* ═══ LOADING OVERLAY ═══ */
    #loading {
      position: absolute; inset: 0;
      background: #000; display: flex;
      flex-direction: column;
      align-items: center; justify-content: center;
      gap: 16px; z-index: 10;
      transition: opacity .4s;
    }
    #loading.hidden { opacity: 0; pointer-events: none; }
    .spinner {
      width: 42px; height: 42px;
      border: 4px solid #333;
      border-top-color: #e94560;
      border-radius: 50%;
      animation: spin .7s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    #loading p { color: #777; font-size: .9rem; }

    /* ═══ CLAVIER VIRTUEL ═══ */
    #vkb {
      position: fixed; bottom: 0; left: 18px; right: 0;
      background: #13132a; border-top: 2px solid #2a2a5a;
      padding: 8px 6px 10px;
      display: none; flex-direction: column; gap: 5px;
      z-index: 200; user-select: none;
    }
    #vkb.visible { display: flex; }
    .kb-row { display: flex; gap: 4px; justify-content: center; flex-wrap: wrap; }
    .kb-key {
      min-width: 32px; height: 36px;
      background: #2c2c4a; border: 1px solid #3a3a6a;
      border-radius: 6px; color: #ddd;
      font-size: .82rem; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      padding: 0 6px; transition: background .1s; flex-shrink: 0;
    }
    .kb-key:hover  { background: #3a3a6a; }
    .kb-key:active { background: #e94560; }
    .kb-key.wide   { min-width: 58px; }
    .kb-key.wider  { min-width: 80px; }
    .kb-key.active-mod { background: #e94560; border-color: #e94560; }
  </style>
</head>
<body>

<!-- ░░ SIDEBAR ░░ -->
<div id="sidebar">
  <div id="sidebar-toggle" onclick="toggleSidebar()" title="Menu">☰</div>
  <div id="sidebar-content">

    <div class="panel-section">
      <span class="panel-title">🖼 Qualité JPEG</span>
      <div class="slider-row">
        <input type="range" id="sl-quality" min="10" max="90" value="35"
               oninput="onQuality(this.value)">
        <span class="slider-val" id="lbl-quality">35%</span>
      </div>
    </div>

    <div class="panel-section">
      <span class="panel-title">⏱ Fréquence</span>
      <div class="slider-row">
        <input type="range" id="sl-fps" min="1" max="20" value="10"
               oninput="onFps(this.value)">
        <span class="slider-val" id="lbl-fps">10 fps</span>
      </div>
    </div>

    <div class="panel-section">
      <span class="panel-title">⌨ Clavier</span>
      <button class="side-btn" id="btn-vkb" onclick="toggleKeyboard()">
        Ouvrir le clavier virtuel
      </button>
    </div>

    <div class="panel-section">
      <span class="panel-title">🌐 Navigation</span>
      <button class="side-btn" onclick="changeSite()">Changer de site…</button>
    </div>

  </div>
</div>

<!-- ░░ ZONE PRINCIPALE ░░ -->
<div id="main">
  <div id="bar">
    <a href="/">⬅ Accueil</a>
    <span>|</span>
    <span id="url-display">📡 {{ url }}</span>
    <span id="status">⏳ Connexion…</span>
    <span id="fps-label">-- fps</span>
    <span id="ping-label"></span>
  </div>

  <div id="viewport">
    <!-- Spinner affiché jusqu'à la 1ère frame -->
    <div id="loading">
      <div class="spinner"></div>
      <p>Chargement de la page distante…</p>
    </div>
    <img id="stream" alt="">
  </div>
</div>

<!-- ░░ CURSEUR ░░ -->
<div id="cursor"></div>

<!-- ░░ CLAVIER VIRTUEL ░░ -->
<div id="vkb"></div>


<script>
/* ═══════════════════════════════════════════════════════
   WEBSOCKET FRAMES  (serveur → client, binaire)
   WEBSOCKET EVENTS  (client → serveur, JSON texte)
═══════════════════════════════════════════════════════ */
const proto = location.protocol === 'https:' ? 'wss' : 'ws';
const host  = location.host;

const imgEl    = document.getElementById('stream');
const cursor   = document.getElementById('cursor');
const fpsEl    = document.getElementById('fps-label');
const pingEl   = document.getElementById('ping-label');
const statusEl = document.getElementById('status');
const loadingEl= document.getElementById('loading');
const vkbEl    = document.getElementById('vkb');
const sidebar  = document.getElementById('sidebar');

let prevObjectUrl = null;

/* ── WS frames ─────────────────────────────────────── */
let wsF;
function connectFrameWS() {
  wsF = new WebSocket(proto + '://' + host + '/ws/stream');
  wsF.binaryType = 'blob';

  wsF.onopen = () => {
    statusEl.textContent = '✅ Connecté';
    statusEl.style.color = '#6f6';
  };

  wsF.onmessage = (ev) => {
    /* ev.data est un Blob JPEG — on crée une URL objet locale */
    const url = URL.createObjectURL(ev.data);
    imgEl.src = url;
    /* Libérer la précédente pour éviter les fuites mémoire */
    if (prevObjectUrl) URL.revokeObjectURL(prevObjectUrl);
    prevObjectUrl = url;

    /* Cacher le spinner dès la 1ère frame */
    loadingEl.classList.add('hidden');

    /* Compteur FPS */
    countFrame();

    /* Mesure latence ping/pong */
    if (pendingPing !== null) {
      pingEl.textContent = (Date.now() - pendingPing) + ' ms';
      pendingPing = null;
    }
  };

  wsF.onclose = () => {
    statusEl.textContent = '🔴 Reconnexion frames…';
    statusEl.style.color  = '#f66';
    setTimeout(connectFrameWS, 2000);
  };
  wsF.onerror = () => wsF.close();
}
connectFrameWS();

/* ── WS events ─────────────────────────────────────── */
let wsE;
function connectEventWS() {
  wsE = new WebSocket(proto + '://' + host + '/ws/events');

  wsE.onclose = () => setTimeout(connectEventWS, 2000);
  wsE.onerror = () => wsE.close();
}
connectEventWS();

function sendEvent(obj) {
  if (wsE && wsE.readyState === WebSocket.OPEN)
    wsE.send(JSON.stringify(obj));
}

/* ── Ping périodique (latence affichée) ────────────── */
let pendingPing = null;
setInterval(() => {
  if (wsE && wsE.readyState === WebSocket.OPEN) {
    pendingPing = Date.now();
    sendEvent({ t: 'ping' });
  }
}, 3000);

/* ═══ COMPTEUR FPS ════════════════════════════════ */
let frames = 0, lastFpsTime = Date.now();
function countFrame() {
  frames++;
  const now = Date.now();
  if (now - lastFpsTime >= 1000) {
    fpsEl.textContent = frames + ' fps';
    frames = 0;
    lastFpsTime = now;
  }
}

/* ═══ SIDEBAR ═════════════════════════════════════ */
function toggleSidebar() { sidebar.classList.toggle('open'); }

function onQuality(v) {
  document.getElementById('lbl-quality').textContent = v + '%';
  sendEvent({ t: 'set_quality', value: parseInt(v) });
}
function onFps(v) {
  document.getElementById('lbl-fps').textContent = v + ' fps';
  sendEvent({ t: 'set_fps', value: parseInt(v) });
}
function changeSite() {
  const url = prompt('Entrez la nouvelle URL :', 'https://');
  if (url && url.trim()) window.location.href = '/?' + encodeURIComponent(url.trim());
}

/* ═══ SOURIS ══════════════════════════════════════ */
function relPos(e) {
  const r  = imgEl.getBoundingClientRect();
  const nw = imgEl.naturalWidth  || 1024;
  const nh = imgEl.naturalHeight || 576;
  return {
    x: Math.round((e.clientX - r.left) / r.width  * nw),
    y: Math.round((e.clientY - r.top)  / r.height * nh)
  };
}

let moveThrottle = 0;
document.addEventListener('mousemove', e => {
  cursor.style.left = e.clientX + 'px';
  cursor.style.top  = e.clientY + 'px';
  const now = Date.now();
  if (now - moveThrottle < 40) return;   // max 25 msg/s
  moveThrottle = now;
  sendEvent({ t: 'move', ...relPos(e) });
});

imgEl.addEventListener('click', e => {
  cursor.classList.add('clicking');
  setTimeout(() => cursor.classList.remove('clicking'), 120);
  sendEvent({ t: 'click', ...relPos(e), btn: 'left' });
});

imgEl.addEventListener('contextmenu', e => {
  e.preventDefault();
  sendEvent({ t: 'click', ...relPos(e), btn: 'right' });
});

imgEl.addEventListener('wheel', e => {
  e.preventDefault();
  sendEvent({ t: 'scroll', dx: e.deltaX, dy: e.deltaY });
}, { passive: false });

/* ═══ CLAVIER VIRTUEL ═════════════════════════════ */
let shiftOn = false;
const KB_ROWS = [
  ['1','2','3','4','5','6','7','8','9','0'],
  ['a','z','e','r','t','y','u','i','o','p'],
  ['q','s','d','f','g','h','j','k','l','m'],
  ['SHIFT','w','x','c','v','b','n',',','.','BACK'],
  ['SPACE','ENTER']
];

function buildKeyboard() {
  vkbEl.innerHTML = '';
  KB_ROWS.forEach(row => {
    const rowEl = document.createElement('div');
    rowEl.className = 'kb-row';
    row.forEach(k => {
      const btn = document.createElement('button');
      btn.className = 'kb-key';
      if      (k === 'SPACE') { btn.textContent = '␣ Espace'; btn.classList.add('wider'); }
      else if (k === 'ENTER') { btn.textContent = '↵ Entrée'; btn.classList.add('wider'); }
      else if (k === 'BACK')  { btn.textContent = '⌫';        btn.classList.add('wide');  }
      else if (k === 'SHIFT') {
        btn.textContent = '⇧ Shift'; btn.classList.add('wide');
        if (shiftOn) btn.classList.add('active-mod');
        btn.id = 'kb-shift';
      } else {
        btn.textContent = shiftOn ? k.toUpperCase() : k;
      }
      btn.addEventListener('click', () => pressKey(k, btn));
      rowEl.appendChild(btn);
    });
    vkbEl.appendChild(rowEl);
  });
}

function pressKey(k, btn) {
  if (k === 'SHIFT') { shiftOn = !shiftOn; buildKeyboard(); return; }
  let key, text;
  if      (k === 'SPACE') { key = 'Space';     text = ' ';  }
  else if (k === 'ENTER') { key = 'Enter';     text = null; }
  else if (k === 'BACK')  { key = 'Backspace'; text = null; }
  else { text = shiftOn ? k.toUpperCase() : k; key = text;  }
  sendEvent({ t: 'key', key, text });
  if (shiftOn && k !== 'SHIFT') { shiftOn = false; buildKeyboard(); }
  btn.classList.add('active-mod');
  setTimeout(() => btn.classList.remove('active-mod'), 130);
}

function toggleKeyboard() {
  const visible = vkbEl.classList.toggle('visible');
  const btn = document.getElementById('btn-vkb');
  if (visible) {
    buildKeyboard();
    btn.classList.add('active');
    btn.textContent = 'Fermer le clavier';
  } else {
    btn.classList.remove('active');
    btn.textContent = 'Ouvrir le clavier virtuel';
  }
}
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
#  NAVIGATEUR HEADLESS
# ─────────────────────────────────────────────
def _normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(('http://', 'https://')):
        raw = 'https://' + raw
    return raw


def start_browser(url: str):
    global _playwright, _browser, _page
    from playwright.sync_api import sync_playwright
    with _page_lock:
        try:
            if _browser:    _browser.close()
        except Exception: pass
        try:
            if _playwright: _playwright.stop()
        except Exception: pass
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-first-run',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync',
                '--disable-translate',
                '--mute-audio',
            ]
        )
        # ← Résolution réduite : beaucoup plus rapide à capturer
        _page = _browser.new_page(viewport={'width': 1024, 'height': 576})
        _page.goto(_normalize_url(url), wait_until='domcontentloaded', timeout=30_000)


def get_page():
    return _page


# ─────────────────────────────────────────────
#  THREAD DE CAPTURE
# ─────────────────────────────────────────────
def _capture_loop():
    global _last_shot
    while True:
        page = get_page()
        if page:
            try:
                with _params_lock:
                    q = _quality
                    s = _sleep
                shot = page.screenshot(
                    type='jpeg',
                    quality=q,
                    full_page=False,
                    clip={'x': 0, 'y': 0, 'width': 1024, 'height': 576},
                )
                with _shot_lock:
                    _last_shot = shot
                _shot_event.set()    # ← réveille tous les WS en attente
            except Exception:
                pass
            time.sleep(s)
        else:
            time.sleep(0.05)


threading.Thread(target=_capture_loop, daemon=True).start()


# ─────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────
@app.route('/')
def index():
    raw_qs = request.query_string.decode('utf-8')
    if not raw_qs:
        return render_template_string(LANDING_HTML)
    t = threading.Thread(target=start_browser, args=(raw_qs,), daemon=True)
    t.start()
    t.join(timeout=30)
    return render_template_string(VIEWER_HTML, url=raw_qs)


@app.route('/ping')
def ping():
    return 'pong', 200


# ─────────────────────────────────────────────
#  WEBSOCKET — STREAM (server → client, binaire)
# ─────────────────────────────────────────────
@sock.route('/ws/stream')
def ws_stream(ws):
    """
    Pousse chaque nouvelle frame JPEG en binaire dès qu'elle est disponible.
    Le client ne demande rien : le serveur envoie en push pur.
    """
    last_sent = None
    while True:
        # Attend qu'une nouvelle frame soit capturée (max 2s pour éviter le blocage)
        _shot_event.wait(timeout=2.0)
        _shot_event.clear()

        with _shot_lock:
            frame = _last_shot

        if frame and frame is not last_sent:
            try:
                ws.send(frame)           # ← envoi binaire direct
                last_sent = frame
            except Exception:
                break


# ─────────────────────────────────────────────
#  WEBSOCKET — EVENTS (client → server, JSON)
# ─────────────────────────────────────────────
@sock.route('/ws/events')
def ws_events(ws):
    global _quality, _sleep
    while True:
        try:
            data = ws.receive(timeout=60)
            if data is None:
                break
            msg  = json.loads(data)
            page = get_page()
            t    = msg.get('t')

            if t == 'ping':
                pass   # le client mesure le RTT lui-même

            elif t == 'move' and page:
                page.mouse.move(float(msg['x']), float(msg['y']))

            elif t == 'click' and page:
                page.mouse.click(float(msg['x']), float(msg['y']),
                                 button=msg.get('btn', 'left'))

            elif t == 'scroll' and page:
                page.mouse.wheel(float(msg.get('dx', 0)),
                                 float(msg.get('dy', 0)))

            elif t == 'key' and page:
                key  = msg.get('key', '')
                text = msg.get('text')
                if key in ('Backspace', 'Enter'):
                    page.keyboard.press(key)
                elif text:
                    page.keyboard.type(text)

            elif t == 'set_quality':
                with _params_lock:
                    _quality = max(10, min(90, int(msg.get('value', 35))))

            elif t == 'set_fps':
                fps = max(1, min(20, int(msg.get('value', 10))))
                with _params_lock:
                    _sleep = 1.0 / fps

        except Exception:
            break


# ─────────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)
