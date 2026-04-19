## Projet complet, prêt à déployer sur Render.com.

 ## 🚀 Comment déployer sur Render.com
1. Sur [render.com](https://render.com) → **New > Web Service** → connectez votre dépôt.
2. Render lira le `render.yaml` automatiquement. Vérifiez que :
   - **Build command** : `pip install -r requirements.txt && playwright install chromium --with-deps`
   - **Start command** : `python app.py`
3. **Déployez** et attendez ~3 min (Playwright télécharge Chromium ≈120 Mo).

---

## 🧠 Architecture résumée

```
Utilisateur (vieux PC)
       │
       │  MJPEG stream (HTTP — juste des JPEG à la suite)
       │◄──────────────────────────────────┐
       │                                   │
       │  WebSocket (souris uniquement)    │
       │──── { t:'move', x, y } ──────────►│
       │──── { t:'click', x, y, btn } ────►│  Flask / flask-sock
                                           │
                                    Playwright (Chromium headless)
                                    • visite l'URL demandée
                                    • screenshot JPEG q=40 toutes 80ms
                                    • applique les mouvements souris
```

---

## ⚙️ Paramètres facilement ajustables

| Paramètre | Emplacement | Valeur par défaut |
|---|---|---|
| Qualité JPEG | `quality=40` | 40 % |
| FPS max | `time.sleep(0.08)` | ~12 fps |
| Résolution | `viewport={'width': 1280, 'height': 720}` | 1280×720 |
| Throttle souris | `if now - moveThrottle < 33` | 30 msg/s |

Pour les **très vieux PC**, vous pouvez baisser à `quality=25`, `sleep(0.15)` et `width=800, height=600`.

### La sidebar
- **Fermée** : une simple languette rouge de 18px sur le côté
- **Ouverte** : 230px avec les 4 panneaux, animation CSS fluide
- Fonctionne aussi sur **petit écran**

### 🖼 Qualité JPEG
- Slider `10% → 95%`, valeur initiale `60%`
- Envoie `{ t:'set_quality', value:65 }` via WebSocket
- Le thread de capture lit `_quality` à chaque frame

### ⏱ FPS
- Slider `1 → 25 fps`, valeur initiale `12 fps`
- Envoie `{ t:'set_fps', value:5 }` → convertit en `_sleep = 1/fps`

### ⌨ Clavier virtuel
```
[ 1 ][ 2 ][ 3 ][ 4 ][ 5 ][ 6 ][ 7 ][ 8 ][ 9 ][ 0 ]
[ a ][ z ][ e ][ r ][ t ][ y ][ u ][ i ][ o ][ p ]
[ q ][ s ][ d ][ f ][ g ][ h ][ j ][ k ][ l ][ m ]
[⇧ Shift][ w ][ x ][ c ][ v ][ b ][ n ][ , ][ . ][ ⌫ ]
[  ␣ Espace  ][  ↵ Entrée  ]
```
- **Shift** : bascule en majuscules puis se relâche automatiquement après une frappe
- **⌫** envoie `Backspace`, **↵** envoie `Enter`

### 🌐 Changer de site
- `prompt()` natif du navigateur → simple et léger
- Redirige vers `/?nouvelle-url`
