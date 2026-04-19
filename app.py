"""
Proxy navigateur léger — Render.com
- Stream MJPEG (screenshots JPEG compressés) → Client
- Retour Client → seulement position souris + clics (WebSocket)
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
#  État global (session unique)
# ─────────────────────────────────────────────
_playwright  = None
_browser     = None
_page        = None
_page_lock   = threading.Lock()   # pour init/reset uniquement
_last_shot   = None               # dernier screenshot en mémoire
_shot_lock   = threading.Lock()

# ─────────────────────────────────────────────
#  PAGE D'ACCUEIL  (aucun paramètre)
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
      background: #0f0f1a;
      color: #e0e0e0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100vh;
      gap: 1.5rem;
    }
    h1 { font-size: 1.8rem; }
    p  { font-size: .9rem; color: #888; }
    form {
      display: flex;
      gap: .5rem;
      width: min(500px, 92vw);
    }
    input {
      flex: 1;
      padding: .65rem 1rem;
      border: 1px solid #333;
      border-radius: 6px;
      background: #1c1c2e;
      color: #fff;
      font-size: 1rem;
    }
    input:focus { outline: 2px solid #e94560; }
    button {
      padding: .65rem 1.4rem;
      background: #e94560;
      border: none;
      border-radius: 6px;
      color: #fff;
      font-size: 1rem;
      cursor: pointer;
    }
    button:hover { background: #c73652; }
    .hint { font-size: .78rem; color: #555; }
  </style>
</head>
<body>
  <h1>🌐 Proxy navigateur léger</h1>
  <p>Entrez une URL pour la consulter via ce proxy.</p>
  <form onsubmit="navigate(event)">
    <input id="u" type="text" placeholder="exemple.com ou https://exemple.com" autofocus>
    <button type="submit">Aller</button>
  </form>
  <span class="hint">Astuce : vous pouvez aussi écrire directement <code>/?exemple.com</code> dans la barre d'adresse.</span>
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
#  PAGE VIEWER  (stream + souris)
# ─────────────────────────────────────────────
VIEWER_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ url }}</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #000; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

    /* ── Barre du haut ── */
    #bar {
      display: flex;
      align-items: center;
      gap: 8px;
      background: #1a1a2e;
      color: #ccc;
      padding: 4px 10px;
      font-size: .8rem;
      flex-shrink: 0;
    }
    #bar a { color: #7eb8f7; text-decoration: none; }
    #bar a:hover { text-decoration: underline; }
    #url-display { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    #fps-label { margin-left: auto; color: #6f6; font-size: .75rem; white-space: nowrap; }
    #status    { color: #fa0; font-size: .75rem; white-space: nowrap; }

    /* ── Zone de stream ── */
    #viewport {
      flex: 1;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      overflow: hidden;
      position: relative;
      cursor: none;       /* on dessine notre propre curseur */
    }
    #stream {
      max-width: 100%;
      max-height: 100%;
      display: block;
      object-fit: contain;
      /* Rendu net sur vieux PC même en downscale */
      image-rendering: optimizeSpeed;
    }

    /* ── Curseur personnalisé ── */
    #cursor {
      position: fixed;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: rgba(233, 69, 96, .85);
      border: 2px solid #fff;
      pointer-events: none;
      transform: translate(-50%, -50%);
      z-index: 100;
      transition: background .1s;
    }
    #cursor.clicking { background: #fff; }
  </style>
</head>
<body>
  <!-- Barre de navigation -->
  <div id="bar">
    <a href="/">⬅ Accueil</a>
    <span>|</span>
    <span id="url-display">📡 {{ url }}</span>
    <span id="status">⏳ Connexion…</span>
    <span id="fps-label">-- fps</span>
  </div>

  <!-- Zone de stream -->
  <div id="viewport">
    <img id="stream" src="/stream" alt="stream en cours…">
  </div>

  <!-- Curseur overlay -->
  <div id="cursor"></div>

  <script>
    /* ── Références DOM ── */
    const imgEl   = document.getElementById('stream');
    const cursor  = document.getElementById('cursor');
    const fpsEl   = document.getElementById('fps-label');
    const statusEl= document.getElementById('status');

    /* ── Compteur FPS basé sur les chargements d'image ── */
    let frames = 0, lastFpsTime = Date.now();
    imgEl.addEventListener('load', () => {
      frames++;
      const now = Date.now();
      if (now - lastFpsTime >= 1000) {
        fpsEl.textContent = frames + ' fps';
        frames = 0;
        lastFpsTime = now;
      }
    });

    /* ── WebSocket pour les événements souris ── */
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let ws;

    function connectWS() {
      ws = new WebSocket(proto + '://' + location.host + '/ws');

      ws.onopen  = () => { statusEl.textContent = '✅ Connecté'; statusEl.style.color = '#6f6'; };
      ws.onclose = () => {
        statusEl.textContent = '🔴 Déconnecté — reconnexion…';
        statusEl.style.color = '#f66';
        setTimeout(connectWS, 2000);
      };
      ws.onerror = () => ws.close();
    }
    connectWS();

    function send(obj) {
      if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify(obj));
    }

    /* ── Coordonnées relatives à la résolution réelle du navigateur headless ── */
    function relPos(e) {
      const r  = imgEl.getBoundingClientRect();
      const nw = imgEl.naturalWidth  || 1280;
      const nh = imgEl.naturalHeight || 720;
      return {
        x: Math.round((e.clientX - r.left) / r.width  * nw),
        y: Math.round((e.clientY - r.top)  / r.height * nh)
      };
    }

    /* ── Déplacement souris ── */
    let moveThrottle = 0;
    document.addEventListener('mousemove', e => {
      /* Déplacer le curseur overlay */
      cursor.style.left = e.clientX + 'px';
      cursor.style.top  = e.clientY + 'px';

      /* Envoyer au serveur (max 30 msg/s) */
      const now = Date.now();
      if (now - moveThrottle < 33) return;
      moveThrottle = now;

      const p = relPos(e);
      send({ t: 'move', x: p.x, y: p.y });
    });

    /* ── Clic gauche ── */
    imgEl.addEventListener('click', e => {
      const p = relPos(e);
      cursor.classList.add('clicking');
      setTimeout(() => cursor.classList.remove('clicking'), 120);
      send({ t: 'click', x: p.x, y: p.y, btn: 'left' });
    });

    /* ── Clic droit ── */
    imgEl.addEventListener('contextmenu', e => {
      e.preventDefault();
      const p = relPos(e);
      send({ t: 'click', x: p.x, y: p.y, btn: 'right' });
    });

    /* ── Défilement (scroll) ── */
    imgEl.addEventListener('wheel', e => {
      e.preventDefault();
      send({ t: 'scroll', dx: e.deltaX, dy: e.deltaY });
    }, { passive: false });
  </script>
</body>
</html>"""


# ─────────────────────────────────────────────
#  GESTION DU NAVIGATEUR HEADLESS
# ─────────────────────────────────────────────

def _normalize_url(raw: str) -> str:
    """Ajoute https:// si absent."""
    raw = raw.strip()
    if not raw.startswith(('http://', 'https://')):
        raw = 'https://' + raw
    return raw


def start_browser(url: str):
    """Lance (ou redémarre) la session Playwright/Chromium."""
    global _playwright, _browser, _page

    from playwright.sync_api import sync_playwright

    with _page_lock:
        # Ferme la session précédente proprement
        try:
            if _browser:  _browser.close()
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
            ]
        )
        _page = _browser.new_page(viewport={'width': 1280, 'height': 720})
        _page.goto(_normalize_url(url), wait_until='domcontentloaded', timeout=20_000)


def get_page():
    return _page


# ─────────────────────────────────────────────
#  THREAD DE CAPTURE — alimente _last_shot
# ─────────────────────────────────────────────

def _capture_loop():
    """Tourne en arrière-plan, prend des screenshots toutes les ~80 ms (≈12 fps)."""
    global _last_shot
    while True:
        page = get_page()
        if page:
            try:
                shot = page.screenshot(
                    type='jpeg',
                    quality=40,        # 40 % : très léger, vieux PC OK
                    full_page=False,
                    clip={'x': 0, 'y': 0, 'width': 1280, 'height': 720},
                )
                with _shot_lock:
                    _last_shot = shot
            except Exception:
                pass
        time.sleep(0.08)   # ≈ 12 fps max


threading.Thread(target=_capture_loop, daemon=True).start()


# ─────────────────────────────────────────────
#  ROUTES FLASK
# ─────────────────────────────────────────────

@app.route('/')
def index():
    """
    /?           → page d'accueil
    /?exemple.com → ouvre exemple.com dans le navigateur headless
    """
    raw_qs = request.query_string.decode('utf-8')

    if not raw_qs:
        return render_template_string(LANDING_HTML)

    # Lance le navigateur en arrière-plan puis affiche le viewer
    t = threading.Thread(target=start_browser, args=(raw_qs,), daemon=True)
    t.start()
    t.join(timeout=25)   # attend max 25 s que la page se charge
    return render_template_string(VIEWER_HTML, url=raw_qs)


@app.route('/stream')
def stream():
    """
    Endpoint MJPEG — compatible <img src="/stream">.
    Très efficace : simple HTTP, pas de JS requis côté client pour afficher l'image.
    """
    def generate():
        while True:
            with _shot_lock:
                frame = _last_shot
            if frame:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n'
                    + frame +
                    b'\r\n'
                )
            else:
                time.sleep(0.05)

    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma':        'no-cache',
        }
    )


@app.route('/ping')
def ping():
    """Health check pour Render.com."""
    return 'pong', 200


# ─────────────────────────────────────────────
#  WEBSOCKET — souris uniquement
# ─────────────────────────────────────────────

@sock.route('/ws')
def ws_handler(ws):
    """
    Reçoit du client :
      { t:'move',   x, y }
      { t:'click',  x, y, btn:'left'|'right' }
      { t:'scroll', dx, dy }
    """
    while True:
        try:
            data = ws.receive(timeout=60)
            if data is None:
                break

            msg  = json.loads(data)
            page = get_page()
            if not page:
                continue

            t = msg.get('t')

            if t == 'move':
                page.mouse.move(float(msg['x']), float(msg['y']))

            elif t == 'click':
                btn = msg.get('btn', 'left')
                page.mouse.click(float(msg['x']), float(msg['y']), button=btn)

            elif t == 'scroll':
                page.mouse.wheel(float(msg.get('dx', 0)), float(msg.get('dy', 0)))

        except Exception:
            break


# ─────────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)
