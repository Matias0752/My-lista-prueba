#!/usr/bin/env python3
"""
scripts/m3u_autofix.py
════════════════════════════════════════════════════════════════
Versión mejorada con scraping directo para canales sin sustituto.
Lee usuario, repo y token desde variables de entorno de GitHub Actions.
════════════════════════════════════════════════════════════════
"""

import re
import time
import base64
import os
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

# ══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN_SECRET", "")
GITHUB_USER   = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
_repo_full    = os.environ.get("GITHUB_REPOSITORY", "/")
GITHUB_REPO   = _repo_full.split("/")[-1]
GITHUB_BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
GITHUB_FILE   = "lista.m3u"

RAW_URL = (
    f"https://raw.githubusercontent.com"
    f"/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}"
)

TIMEOUT      = 10
MAX_WORKERS  = 20
SCORE_MINIMO = 0.72

OUTPUT_HTML = "reporte.html"
OUTPUT_M3U  = GITHUB_FILE

# ── Listas propias extra ──────────────────────────────────────
LISTAS_PROPIAS = []

# ── Listas públicas ───────────────────────────────────────────
LISTAS_PUBLICAS = [
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cl.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/ar.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/es.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/mx.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/latam.m3u",
]

# ── Páginas web oficiales generales ──────────────────────────
PAGINAS_CANALES = {
    "TVN":      "https://www.tvn.cl/envivo",
    "Canal 13": "https://www.canal13.cl/en-vivo",
    "Mega":     "https://www.mega.cl/en-vivo",
    "CHV":      "https://www.chilevision.cl/en-vivo",
    "T13":      "https://www.t13.cl/en-vivo",
    "24 Horas": "https://www.24horas.cl/en-vivo",
    "Bio Bio":  "https://www.biobiochile.cl/lista/television/en-vivo",
    "La Red":   "https://www.lared.cl/en-vivo",
    "TV+":      "https://www.tvmas.cl/en-vivo",
    "ADN TV":   "https://www.adnradio.cl/television",
}

# ── URLs directas conocidas para canales problemáticos ───────
# Si el stream está hardcodeado aquí, se usa directamente
# sin necesidad de scraping. Actualiza cuando cambien.
URLS_DIRECTAS = {
    # Chilenas
    "tvn 24 horas":   "https://mdstrm.com/live-stream-playlist/5653641561b4eba30a7e4929.m3u8",
    "canal 13":       "https://mdstrm.com/live-stream-playlist/57bf54f97b4f91f72e000000.m3u8",
    "carolina tv":    "https://mdstrm.com/live-stream-playlist/63a06468117f42713374addd.m3u8",
    "chv":            "https://mdstrm.com/live-stream-playlist/5609a6b77b4f91e33b000002.m3u8",
    "chilevision":    "https://mdstrm.com/live-stream-playlist/5609a6b77b4f91e33b000002.m3u8",
    "via x":          "https://mdstrm.com/live-stream-playlist/57bff4f27b4f91f72e000002.m3u8",
    "etc tv":         "https://mdstrm.com/live-stream-playlist/5aa7e0cb8cac24bb7c000004.m3u8",
    "radio romantica": "https://mdstrm.com/live-stream-playlist/63a0674c1137d408b45d4821.m3u8",
    "ztv anime":      "https://mitv.zplay.cl/Ztv/index.m3u8",
    "chile informa":  "http://45.225.95.154:8081/mcg/chileinforma/playlist.m3u8",
    "corcubio":       "http://45.225.95.154:8081/mcg/corcubio/playlist.m3u8",
    # Deportes ESPN (fuentes alternativas públicas)
    "espn":           "https://cf-hls-media.sportsnet.ca/hls/live/2042975/SNET_ESPN/master.m3u8",
    "espn 2":         "https://cf-hls-media.sportsnet.ca/hls/live/2042976/SNET_ESPN2/master.m3u8",
    "espn 3":         "https://cf-hls-media.sportsnet.ca/hls/live/2042977/SNET_ESPN3/master.m3u8",
    "espn premium":   "https://cf-hls-media.sportsnet.ca/hls/live/2042975/SNET_ESPN/master.m3u8",
    "espn 4":         "https://cf-hls-media.sportsnet.ca/hls/live/2042978/SNET_ESPN4/master.m3u8",
    # Infantiles
    "disney channel": "https://linear-472.frequency.stream/dist/aws/linear-472/master.m3u8",
    "disney jr":      "https://linear-476.frequency.stream/dist/aws/linear-476/master.m3u8",
    "nick":           "https://linear-479.frequency.stream/dist/aws/linear-479/master.m3u8",
    "discovery kids": "https://linear-480.frequency.stream/dist/aws/linear-480/master.m3u8",
}

# ── Páginas de scraping para canales específicos sin sustituto ─
PAGINAS_ESPECIFICAS = {
    "TVN 24 Horas":    "https://www.24horas.cl/en-vivo",
    "Canal 13":        "https://www.canal13.cl/en-vivo",
    "Carolina TV":     "https://www.carolina.cl/envivo",
    "CL: CHV Full HD": "https://www.chilevision.cl/en-vivo",
    "CL: Via X":       "https://www.viax.cl/en-vivo",
    "CL: Via X 2":     "https://www.viax.cl/en-vivo",
    "CL: ETC TV":      "https://www.etc.cl/streaming",
    "Radio Romántica TV": "https://www.carolina.cl/envivo",
    "ZTV Anime":       "https://mitv.zplay.cl",
    "Chile Informa":   "http://www.chileinforma.cl",
    "Corcubio TV":     "https://www.corcubio.cl",
}

# ══════════════════════════════════════════════════════════════
#  LIMPIEZA Y SIMILITUD
# ══════════════════════════════════════════════════════════════

_STOPWORDS = re.compile(
    r'\b(full|hd|fhd|sd|4k|cl|ar|mx|es|co|pe|ve|uy|tv|canal|channel|'
    r'la|el|los|las|de|del|en|vivo|live|stream|online|gratis|free|plus)\b',
    re.IGNORECASE
)

def limpiar_nombre(n):
    n = re.sub(r'^[A-Z]{2}\s*:\s*', '', n)
    n = re.sub(r'\s*(Full HD|FHD|HD\+?|SD|4K|\*+|✪|✅|⭐|\|\s*.+$)', '', n, flags=re.IGNORECASE)
    n = re.sub(r'\[.*?\]', '', n)
    n = re.sub(r'\(.*?\)', '', n)
    n = re.sub(r'\s+\d+$', '', n)
    n = _STOPWORDS.sub('', n)
    return re.sub(r'\s+', ' ', n).strip().lower()

def similitud(a, b):
    if not a or not b:    return 0.0
    if a == b:            return 1.0
    if a in b or b in a:  return 0.92
    return SequenceMatcher(None, a, b).ratio()

def buscar_url_directa(nombre_limpio):
    """Busca en el diccionario URLS_DIRECTAS por similitud de nombre."""
    mejor_score = 0
    mejor_url   = None
    for clave, url in URLS_DIRECTAS.items():
        s = similitud(nombre_limpio, clave)
        if s > mejor_score:
            mejor_score = s
            mejor_url   = url
    if mejor_score >= 0.65:
        return mejor_url
    return None

# ══════════════════════════════════════════════════════════════
#  PARSEO M3U
# ══════════════════════════════════════════════════════════════

def parsear_m3u(contenido):
    canales, lineas = [], contenido.splitlines()
    i = 0
    while i < len(lineas):
        linea = lineas[i].strip()
        if linea.startswith("#EXTINF"):
            meta = linea
            j = i + 1
            while j < len(lineas) and (
                not lineas[j].strip() or lineas[j].strip().startswith("#EXTVLCOPT")
            ):
                j += 1
            if j < len(lineas):
                url = lineas[j].strip()
                if url and not url.startswith("#"):
                    m_nombre = re.search(r',(.+)$', meta)
                    nombre   = m_nombre.group(1).strip() if m_nombre else "Sin nombre"
                    m_grupo  = re.search(r'group-title="([^"]*)"', meta)
                    grupo    = m_grupo.group(1).strip() if m_grupo else "Sin grupo"
                    m_logo   = re.search(r'tvg-logo="([^"]*)"', meta)
                    logo     = m_logo.group(1).strip() if m_logo else ""
                    canales.append({
                        "nombre":           nombre,
                        "nombre_limpio":    limpiar_nombre(nombre),
                        "grupo":            grupo,
                        "logo":             logo,
                        "url":              url,
                        "url_original":     url,
                        "meta":             meta,
                        "estado":           None,
                        "codigo":           None,
                        "ms":               None,
                        "sustituido":       False,
                        "sustituto_fuente": None,
                    })
                i = j
        i += 1
    return canales

# ══════════════════════════════════════════════════════════════
#  VERIFICACIÓN
# ══════════════════════════════════════════════════════════════

def verificar_url(url, timeout=None):
    t = timeout or TIMEOUT
    inicio = time.time()
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"},
            timeout=t, stream=True
        )
        ms = int((time.time() - inicio) * 1000)
        r.close()
        estado = "activo" if r.status_code in (200, 206, 301, 302, 303) else "caído"
        return estado, r.status_code, ms
    except requests.exceptions.Timeout:
        return "timeout", "TIMEOUT", t * 1000
    except Exception as e:
        return "error", str(e)[:50], int((time.time() - inicio) * 1000)

def validar_todos(canales):
    total, completados = len(canales), 0
    print(f"\nValidando {total} canales ({MAX_WORKERS} hilos)...\n")
    resultados = [None] * total

    def _v(canal):
        estado, codigo, ms = verificar_url(canal["url"])
        canal.update({"estado": estado, "codigo": codigo, "ms": ms})
        return canal

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futuros = {ex.submit(_v, c): i for i, c in enumerate(canales)}
        for fut in as_completed(futuros):
            idx = futuros[fut]
            resultados[idx] = fut.result()
            completados += 1
            c = resultados[idx]
            ico = "✅" if c["estado"] == "activo" else "❌"
            print(f"  [{completados:>3}/{total}] {ico} {c['nombre'][:45]:<45} {c['codigo']} ({c['ms']}ms)")
    return resultados

# ══════════════════════════════════════════════════════════════
#  POOL DE SUSTITUTOS (listas públicas + web)
# ══════════════════════════════════════════════════════════════

_cache     = {}
PATRON_M3U = re.compile(
    r'https?://[^\s"\'<>&]+\.m3u8?(?:[?#][^\s"\'<>&]*)?', re.IGNORECASE
)

def _cargar_m3u(fuente):
    if fuente in _cache:
        return _cache[fuente]
    try:
        if fuente.startswith("http"):
            r = requests.get(fuente, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            contenido = r.text
        else:
            with open(fuente, encoding="utf-8", errors="ignore") as f:
                contenido = f.read()
        canales = parsear_m3u(contenido)
        print(f"    ✔ {len(canales):>4} entradas ← {fuente[:70]}")
        _cache[fuente] = canales
        return canales
    except Exception as e:
        print(f"    ✗ {fuente[:60]}: {e}")
        _cache[fuente] = []
        return []

def _scrape_web(nombre, url):
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept":     "text/html,*/*",
            "Referer":    url,
        })
        urls = [
            u for u in set(PATRON_M3U.findall(r.text))
            if not re.search(r'(\d+\.ts|seg-\d+|chunk-\d+)', u, re.I)
        ]
        if urls:
            print(f"    ✔ {len(urls):>4} stream(s) ← web: {nombre}")
        return [{
            "nombre":        nombre,
            "nombre_limpio": limpiar_nombre(nombre),
            "grupo":         "Web Oficial",
            "logo":          "",
            "url":           u,
            "url_original":  u,
            "meta":          f"#EXTINF:-1,{nombre}",
        } for u in urls]
    except Exception as e:
        print(f"    ✗ web {nombre}: {e}")
        return []

def construir_pool():
    pool = []
    print("\n  Cargando listas públicas (iptv-org)...")
    for url in LISTAS_PUBLICAS:
        pool += _cargar_m3u(url)
    if LISTAS_PROPIAS:
        print("\n  Cargando listas propias...")
        for src in LISTAS_PROPIAS:
            pool += _cargar_m3u(src)
    if PAGINAS_CANALES:
        print("\n  Scrapeando páginas generales...")
        for nombre, url in PAGINAS_CANALES.items():
            pool += _scrape_web(nombre, url)
    print(f"\n  Pool total: {len(pool)} candidatos")
    return pool

def buscar_sustituto_en_pool(canal, pool):
    candidatos = [
        (similitud(canal["nombre_limpio"],
                   fc.get("nombre_limpio", limpiar_nombre(fc["nombre"]))), fc)
        for fc in pool if fc["url"] != canal["url_original"]
    ]
    candidatos = sorted(
        [(s, fc) for s, fc in candidatos if s >= SCORE_MINIMO],
        key=lambda x: (-x[0], 0 if ".m3u8" in x[1]["url"].lower() else 1)
    )
    vistos = set()
    for score, fc in candidatos[:8]:
        if fc["url"] in vistos:
            continue
        vistos.add(fc["url"])
        estado, _, ms = verificar_url(fc["url"], timeout=8)
        if estado == "activo":
            return {
                "url":               fc["url"],
                "fuente":            fc.get("grupo", "Pool público"),
                "score":             score,
                "nombre_encontrado": fc["nombre"],
                "ms":                ms,
            }
    return None

def buscar_sustituto_scraping(canal):
    """
    Para canales sin sustituto en el pool:
    1. Busca en URLS_DIRECTAS conocidas
    2. Scrapea la página oficial específica del canal
    """
    nombre_limpio = canal["nombre_limpio"]

    # 1. URL directa conocida
    url_directa = buscar_url_directa(nombre_limpio)
    if url_directa:
        print(f"     → Probando URL directa conocida...")
        estado, codigo, ms = verificar_url(url_directa, timeout=8)
        if estado == "activo":
            return {
                "url":               url_directa,
                "fuente":            "URL directa conocida",
                "score":             1.0,
                "nombre_encontrado": canal["nombre"],
                "ms":                ms,
            }

    # 2. Scraping de página oficial específica
    pagina = PAGINAS_ESPECIFICAS.get(canal["nombre"])
    if not pagina:
        # buscar por similitud en las claves
        for clave, url_p in PAGINAS_ESPECIFICAS.items():
            if similitud(nombre_limpio, limpiar_nombre(clave)) >= 0.75:
                pagina = url_p
                break

    if pagina:
        print(f"     → Scrapeando {pagina} ...")
        candidatos = _scrape_web(canal["nombre"], pagina)
        for fc in candidatos:
            estado, _, ms = verificar_url(fc["url"], timeout=8)
            if estado == "activo":
                return {
                    "url":               fc["url"],
                    "fuente":            f"Scraping: {pagina}",
                    "score":             0.9,
                    "nombre_encontrado": canal["nombre"],
                    "ms":                ms,
                }

    return None

# ══════════════════════════════════════════════════════════════
#  GITHUB API
# ══════════════════════════════════════════════════════════════

def actualizar_github(contenido_nuevo):
    if not GITHUB_TOKEN:
        print("\n⚠️  Sin token — guardando solo en disco.")
        return False
    try:
        api     = (f"https://api.github.com/repos"
                   f"/{GITHUB_USER}/{GITHUB_REPO}/contents/{GITHUB_FILE}")
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Content-Type":  "application/json",
        }
        sha = requests.get(api, headers=headers).json()["sha"]
        ts  = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        payload = {
            "message": f"[AutoFix] actualización automática {ts}",
            "content": base64.b64encode(contenido_nuevo.encode()).decode(),
            "sha":     sha,
            "branch":  GITHUB_BRANCH,
        }
        r = requests.put(api, headers=headers, json=payload)
        if r.status_code in (200, 201):
            print(f"✅ GitHub actualizado → {GITHUB_USER}/{GITHUB_REPO}/{GITHUB_FILE}")
            return True
        print(f"❌ Error GitHub {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"❌ Excepción GitHub: {e}")
        return False

# ══════════════════════════════════════════════════════════════
#  SALIDA
# ══════════════════════════════════════════════════════════════

def reconstruir_m3u(canales):
    lineas = ["#EXTM3U\n"]
    for c in canales:
        lineas += [c["meta"], c["url"], ""]
    return "\n".join(lineas)

def guardar_html(canales, path):
    ts   = datetime.now().strftime("%d/%m/%Y %H:%M UTC")
    n_ok = sum(1 for c in canales if c["estado"] == "activo" and not c["sustituido"])
    n_su = sum(1 for c in canales if c["sustituido"])
    n_ko = sum(1 for c in canales if c["estado"] != "activo" and not c["sustituido"])

    filas = ""
    for grupo in sorted(set(c["grupo"] for c in canales)):
        filas += f'<tr class="gr"><td colspan="4">{grupo or "Sin grupo"}</td></tr>\n'
        for c in [x for x in canales if x["grupo"] == grupo]:
            if c["sustituido"]:
                badge = '<span class="b su">⟳ SUSTITUIDO</span>'
            elif c["estado"] == "activo":
                badge = '<span class="b ok">▶ ACTIVO</span>'
            else:
                badge = '<span class="b ko">✕ SIN SUSTITUTO</span>'
            logo  = f'<img src="{c["logo"]}" onerror="this.style.display=\'none\'">' if c["logo"] else ""
            url_s = c["url"][:72] + ("…" if len(c["url"]) > 72 else "")
            nota  = ""
            if c["sustituido"]:
                nota = (f'<br><small style="color:#856404">Orig: {c["url_original"][:60]}</small>'
                        f'<br><small style="color:#777">Fuente: {c["sustituto_fuente"]}</small>')
            filas += (f'<tr><td>{logo}<b>{c["nombre"]}</b></td>'
                      f'<td><code>{url_s}</code>{nota}</td>'
                      f'<td>{badge}</td><td>{c["ms"]}ms</td></tr>\n')

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<title>AutoFix M3U — {ts}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#f4f4f4;color:#222;margin:0;padding:2rem}}
h1{{font-size:1.4rem;margin-bottom:.3rem}}
.stats{{display:flex;gap:1rem;margin-bottom:2rem;flex-wrap:wrap}}
.stat{{background:#fff;border-radius:8px;padding:.8rem 1.2rem;border:1px solid #ddd}}
.stat .n{{font-size:1.8rem;font-weight:700}}
.gn{{color:#2d6a4f}}.yn{{color:#856404}}.rn{{color:#9b2226}}.gy{{color:#555}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
th{{background:#111;color:#fff;padding:.55rem .8rem;text-align:left;font-size:.77rem}}
td{{padding:.48rem .8rem;border-bottom:1px solid #eee;font-size:.79rem;vertical-align:middle}}
td img{{width:26px;height:26px;object-fit:contain;margin-right:8px;vertical-align:middle}}
code{{font-size:.71rem;color:#555;word-break:break-all}}
.b{{padding:3px 10px;border-radius:20px;font-size:.71rem;font-weight:600;white-space:nowrap}}
.ok{{background:#d8f3dc;color:#2d6a4f}}
.su{{background:#fff3cd;color:#856404}}
.ko{{background:#ffe0e0;color:#9b2226}}
.gr td{{background:#ececec;font-weight:600;font-size:.74rem;color:#444;padding:.34rem .8rem}}
tr:hover td{{background:#fafafa}}
</style></head><body>
<h1>📡 M3U AutoFix</h1>
<p style="color:#666;font-size:.82rem">Generado: {ts} | {GITHUB_USER}/{GITHUB_REPO}</p>
<div class="stats">
  <div class="stat"><div class="n gn">{n_ok}</div><div>Activos</div></div>
  <div class="stat"><div class="n yn">{n_su}</div><div>Sustituidos</div></div>
  <div class="stat"><div class="n rn">{n_ko}</div><div>Sin sustituto</div></div>
  <div class="stat"><div class="n gy">{len(canales)}</div><div>Total</div></div>
</div>
<table>
<thead><tr><th>Canal</th><th>URL</th><th>Estado</th><th>Latencia</th></tr></thead>
<tbody>{filas}</tbody>
</table>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📊 Reporte HTML → {path}")

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("═"*62)
    print("  M3U AutoFix v2 — GitHub Actions")
    print(f"  Repo  : {GITHUB_USER}/{GITHUB_REPO}  [{GITHUB_BRANCH}]")
    print(f"  Archivo: {GITHUB_FILE}")
    print("═"*62)

    # 1. Descargar lista
    print(f"\n[1/6] Descargando lista...")
    r = requests.get(RAW_URL, timeout=15)
    r.raise_for_status()
    canales = parsear_m3u(r.text)
    print(f"      {len(canales)} canales cargados.")

    # 2. Validar todos
    print(f"\n[2/6] Validando streams...")
    canales = validar_todos(canales)
    caidos  = [c for c in canales if c["estado"] != "activo"]
    print(f"\n      ✅ Activos : {len(canales) - len(caidos)}")
    print(f"      ❌ Caídos  : {len(caidos)}")

    if not caidos:
        print(f"\n      Todos los canales están activos.")
    else:
        # 3. Construir pool de sustitutos
        print(f"\n[3/6] Construyendo pool de sustitutos (listas públicas + webs)...")
        pool = construir_pool()

        # 4. Primer intento: pool público
        print(f"\n[4/6] Buscando en pool público...\n")
        sin_sustituto = []
        n_sust = 0
        for c in caidos:
            print(f"  → {c['nombre']}")
            res = buscar_sustituto_en_pool(c, pool)
            if res:
                print(f"     ✅ {res['nombre_encontrado']} (score={res['score']:.2f}, {res['ms']}ms)")
                c.update({
                    "url":              res["url"],
                    "estado":           "activo",
                    "sustituido":       True,
                    "sustituto_fuente": res["fuente"],
                    "ms":               res["ms"],
                })
                n_sust += 1
            else:
                print(f"     ⚠️  Sin match en pool — pasando a scraping directo.")
                sin_sustituto.append(c)

        # 5. Segundo intento: scraping directo + URLs conocidas
        n_sust2 = 0
        n_sin   = 0
        if sin_sustituto:
            print(f"\n[5/6] Scraping directo para {len(sin_sustituto)} canal(es) restantes...\n")
            for c in sin_sustituto:
                print(f"  → {c['nombre']}")
                res = buscar_sustituto_scraping(c)
                if res:
                    print(f"     ✅ Encontrado via {res['fuente']} ({res['ms']}ms)")
                    c.update({
                        "url":              res["url"],
                        "estado":           "activo",
                        "sustituido":       True,
                        "sustituto_fuente": res["fuente"],
                        "ms":               res["ms"],
                    })
                    n_sust2 += 1
                else:
                    print(f"     ❌ Sin sustituto disponible.")
                    n_sin += 1
        else:
            print(f"\n[5/6] Ningún canal requirió scraping directo.")

        total_sust = n_sust + n_sust2
        print(f"\n      Pool público : {n_sust} sustituidos")
        print(f"      Scraping directo: {n_sust2} sustituidos")
        print(f"      Sin sustituto   : {n_sin}")

    # 6. Guardar y subir
    contenido = reconstruir_m3u(canales)
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write(contenido)
    print(f"\n[6/6] {OUTPUT_M3U} guardado. Subiendo a GitHub...")
    actualizar_github(contenido)
    guardar_html(canales, OUTPUT_HTML)

    caidos_final = [c for c in canales if c["estado"] != "activo"]
    print("\n" + "═"*62)
    print(f"  ✅ Activos originales : {len(canales) - len(caidos)}")
    print(f"  ⟳  Sustituidos total  : {sum(1 for c in canales if c['sustituido'])}")
    print(f"  ❌ Sin sustituto      : {len(caidos_final)}")
    print(f"  📋 Total              : {len(canales)}")
    print("═"*62)

if __name__ == "__main__":
    main()
