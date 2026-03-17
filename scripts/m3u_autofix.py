#!/usr/bin/env python3
"""
scripts/m3u_autofix.py
════════════════════════════════════════════════════════════════
Versión para GitHub Actions.
El token se lee de la variable de entorno GITHUB_TOKEN_SECRET.
El repo y usuario se leen de las variables de entorno de Actions.
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
#  En GitHub Actions estas variables vienen del entorno.
#  Si ejecutas local, puedes rellenarlas directamente aquí.
# ══════════════════════════════════════════════════════════════

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN_SECRET", "")
GITHUB_USER   = os.environ.get("GITHUB_REPOSITORY_OWNER", "TU_USUARIO")
_repo_full    = os.environ.get("GITHUB_REPOSITORY", f"{GITHUB_USER}/TU_REPO")
GITHUB_REPO   = _repo_full.split("/")[-1]
GITHUB_BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
GITHUB_FILE   = "lista.m3u"          # ← cambia si tu archivo tiene otro nombre

RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}"

TIMEOUT      = 10
MAX_WORKERS  = 20
SCORE_MINIMO = 0.72

OUTPUT_HTML  = "reporte.html"
OUTPUT_M3U   = GITHUB_FILE            # sobreescribe el mismo archivo

# ── Tus listas propias adicionales ────────────────────────────
LISTAS_PROPIAS = [
    # "https://raw.githubusercontent.com/TU/OTRO_REPO/main/backup.m3u",
]

# ── Listas públicas ───────────────────────────────────────────
LISTAS_PUBLICAS = [
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/cl.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/ar.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/es.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/mx.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/latam.m3u",
]

# ── Páginas web oficiales ─────────────────────────────────────
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

# ══════════════════════════════════════════════════════════════
#  LIMPIEZA Y SIMILITUD DE NOMBRES
# ══════════════════════════════════════════════════════════════

_STOPWORDS = re.compile(
    r'\b(full|hd|fhd|sd|4k|cl|ar|mx|es|co|pe|ve|uy|tv|canal|channel|'
    r'la|el|los|las|de|del|en|vivo|live|stream|online|gratis|free|plus)\b',
    re.IGNORECASE
)

def limpiar_nombre(nombre):
    n = re.sub(r'^[A-Z]{2}\s*:\s*', '', nombre)
    n = re.sub(r'\s*(Full HD|FHD|HD\+?|SD|4K|\*+|✪|✅|⭐|\|\s*.+$)', '', n, flags=re.IGNORECASE)
    n = re.sub(r'\s+\d+$', '', n)
    n = _STOPWORDS.sub('', n)
    return re.sub(r'\s+', ' ', n).strip().lower()

def similitud(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    return SequenceMatcher(None, a, b).ratio()

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
            while j < len(lineas) and (not lineas[j].strip() or lineas[j].strip().startswith("#EXTVLCOPT")):
                j += 1
            if j < len(lineas):
                url = lineas[j].strip()
                if url and not url.startswith("#"):
                    nombre = (re.search(r',(.+)$', meta) or type('', (), {'group': lambda s, x: 'Sin nombre'})()).group(1).strip()
                    grupo  = re.search(r'group-title="([^"]*)"', meta)
                    grupo  = grupo.group(1).strip() if grupo else "Sin grupo"
                    logo   = re.search(r'tvg-logo="([^"]*)"', meta)
                    logo   = logo.group(1).strip() if logo else ""
                    canales.append({
                        "nombre": nombre, "nombre_limpio": limpiar_nombre(nombre),
                        "grupo": grupo, "logo": logo,
                        "url": url, "url_original": url, "meta": meta,
                        "estado": None, "codigo": None, "ms": None,
                        "sustituido": False, "sustituto_fuente": None,
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
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"},
                         timeout=t, stream=True)
        ms = int((time.time() - inicio) * 1000)
        r.close()
        return ("activo" if r.status_code in (200, 206, 301, 302, 303) else "caído"), r.status_code, ms
    except requests.exceptions.Timeout:
        return "timeout", "TIMEOUT", t * 1000
    except Exception as e:
        return "error", str(e)[:50], int((time.time() - inicio) * 1000)

def validar_todos(canales):
    total, completados = len(canales), 0
    print(f"\nValidando {total} canales ({MAX_WORKERS} hilos)...\n")
    resultados = [None] * total
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futuros = {ex.submit(lambda c: (c.update(dict(zip(["estado","codigo","ms"], verificar_url(c["url"])))) or c), c): i
                   for i, c in enumerate(canales)}
        for fut in as_completed(futuros):
            idx = futuros[fut]
            resultados[idx] = fut.result()
            completados += 1
            c = resultados[idx]
            print(f"  [{completados:>3}/{total}] {'✅' if c['estado']=='activo' else '❌'} "
                  f"{c['nombre'][:45]:<45} {c['codigo']} ({c['ms']}ms)")
    return resultados

# ══════════════════════════════════════════════════════════════
#  POOL DE SUSTITUTOS
# ══════════════════════════════════════════════════════════════

_cache = {}
PATRON_M3U = re.compile(r'https?://[^\s"\'<>&]+\.m3u8?(?:[?#][^\s"\'<>&]*)?', re.IGNORECASE)

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
            "User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*", "Referer": url})
        urls = [u for u in set(PATRON_M3U.findall(r.text))
                if not re.search(r'(\d+\.ts|seg-\d+|chunk-\d+)', u, re.I)]
        if urls:
            print(f"    ✔ {len(urls):>4} stream(s) ← web: {nombre}")
        return [{"nombre": nombre, "nombre_limpio": limpiar_nombre(nombre),
                 "grupo": "Web Oficial", "logo": "", "url": u, "url_original": u,
                 "meta": f"#EXTINF:-1,{nombre}"} for u in urls]
    except Exception as e:
        print(f"    ✗ web {nombre}: {e}")
        return []

def construir_pool():
    pool = []
    print("\n  Cargando listas públicas...")
    for url in LISTAS_PUBLICAS:
        pool += _cargar_m3u(url)
    if LISTAS_PROPIAS:
        print("\n  Cargando listas propias...")
        for src in LISTAS_PROPIAS:
            pool += _cargar_m3u(src)
    if PAGINAS_CANALES:
        print("\n  Scrapeando páginas oficiales...")
        for nombre, url in PAGINAS_CANALES.items():
            pool += _scrape_web(nombre, url)
    print(f"\n  Pool total: {len(pool)} candidatos")
    return pool

def buscar_sustituto(canal, pool):
    candidatos = sorted(
        [(similitud(canal["nombre_limpio"], fc.get("nombre_limpio", limpiar_nombre(fc["nombre"]))), fc)
         for fc in pool if fc["url"] != canal["url_original"]],
        key=lambda x: (-x[0], 0 if ".m3u8" in x[1]["url"].lower() else 1)
    )
    vistos = set()
    for score, fc in candidatos[:8]:
        if score < SCORE_MINIMO or fc["url"] in vistos:
            continue
        vistos.add(fc["url"])
        estado, _, ms = verificar_url(fc["url"], timeout=8)
        if estado == "activo":
            return {"url": fc["url"], "fuente": fc.get("grupo","?"),
                    "score": score, "nombre_encontrado": fc["nombre"], "ms": ms}
    return None

# ══════════════════════════════════════════════════════════════
#  GITHUB API
# ══════════════════════════════════════════════════════════════

def actualizar_github(contenido_nuevo):
    if not GITHUB_TOKEN:
        print("\n⚠️  Sin token — guardando solo localmente.")
        return False
    try:
        api     = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/json"}
        sha     = requests.get(api, headers=headers).json()["sha"]
        payload = {
            "message": f"[AutoFix] actualización automática {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "content": base64.b64encode(contenido_nuevo.encode()).decode(),
            "sha": sha, "branch": GITHUB_BRANCH,
        }
        r = requests.put(api, headers=headers, json=payload)
        ok = r.status_code in (200, 201)
        print(f"{'✅ GitHub actualizado' if ok else f'❌ Error GitHub {r.status_code}'}")
        return ok
    except Exception as e:
        print(f"❌ Excepción GitHub: {e}")
        return False

# ══════════════════════════════════════════════════════════════
#  HTML + M3U
# ══════════════════════════════════════════════════════════════

def reconstruir_m3u(canales):
    lineas = ["#EXTM3U\n"]
    for c in canales:
        lineas += [c["meta"], c["url"], ""]
    return "\n".join(lineas)

def guardar_html(canales, path):
    ts   = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    n_ok = sum(1 for c in canales if c["estado"]=="activo" and not c["sustituido"])
    n_su = sum(1 for c in canales if c["sustituido"])
    n_ko = sum(1 for c in canales if c["estado"]!="activo" and not c["sustituido"])
    filas = ""
    for grupo in sorted(set(c["grupo"] for c in canales)):
        filas += f'<tr class="gr"><td colspan="4">{grupo or "Sin grupo"}</td></tr>\n'
        for c in [x for x in canales if x["grupo"]==grupo]:
            badge = ('<span class="b su">⟳ SUSTITUIDO</span>' if c["sustituido"] else
                     '<span class="b ok">▶ ACTIVO</span>' if c["estado"]=="activo" else
                     '<span class="b ko">✕ SIN SUSTITUTO</span>')
            logo  = f'<img src="{c["logo"]}" onerror="this.style.display=\'none\'">' if c["logo"] else ""
            nota  = (f'<br><small>Orig: {c["url_original"][:60]}</small>'
                     f'<br><small>Fuente: {c["sustituto_fuente"]}</small>') if c["sustituido"] else ""
            filas += (f'<tr><td>{logo}<b>{c["nombre"]}</b></td>'
                      f'<td><code>{c["url"][:72]}</code>{nota}</td>'
                      f'<td>{badge}</td><td>{c["ms"]}ms</td></tr>\n')
    html = (f'<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>AutoFix M3U</title>'
            f'<style>body{{font-family:system-ui,sans-serif;background:#f4f4f4;padding:2rem}}'
            f'.stats{{display:flex;gap:1rem;margin-bottom:2rem}}.stat{{background:#fff;border-radius:8px;'
            f'padding:.8rem 1.2rem;border:1px solid #ddd}}.stat .n{{font-size:1.8rem;font-weight:700}}'
            f'.gn{{color:#2d6a4f}}.yn{{color:#856404}}.rn{{color:#9b2226}}.gy{{color:#555}}'
            f'table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden}}'
            f'th{{background:#111;color:#fff;padding:.55rem .8rem;text-align:left;font-size:.77rem}}'
            f'td{{padding:.48rem .8rem;border-bottom:1px solid #eee;font-size:.79rem;vertical-align:middle}}'
            f'td img{{width:24px;height:24px;object-fit:contain;margin-right:6px;vertical-align:middle}}'
            f'code{{font-size:.71rem;color:#555;word-break:break-all}}'
            f'.b{{padding:3px 10px;border-radius:20px;font-size:.71rem;font-weight:600}}'
            f'.ok{{background:#d8f3dc;color:#2d6a4f}}.su{{background:#fff3cd;color:#856404}}'
            f'.ko{{background:#ffe0e0;color:#9b2226}}'
            f'.gr td{{background:#ececec;font-weight:600;font-size:.74rem;color:#444;padding:.34rem .8rem}}'
            f'</style></head><body>'
            f'<h1 style="font-size:1.4rem">📡 AutoFix M3U</h1>'
            f'<p style="color:#666;font-size:.82rem">Generado: {ts}</p>'
            f'<div class="stats">'
            f'<div class="stat"><div class="n gn">{n_ok}</div><div>Activos</div></div>'
            f'<div class="stat"><div class="n yn">{n_su}</div><div>Sustituidos</div></div>'
            f'<div class="stat"><div class="n rn">{n_ko}</div><div>Sin sustituto</div></div>'
            f'<div class="stat"><div class="n gy">{len(canales)}</div><div>Total</div></div>'
            f'</div><table><thead><tr><th>Canal</th><th>URL</th><th>Estado</th><th>Latencia</th></tr></thead>'
            f'<tbody>{filas}</tbody></table></body></html>')
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📊 Reporte → {path}")

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("═"*62)
    print("  M3U AutoFix (GitHub Actions)")
    print(f"  Repo : {GITHUB_USER}/{GITHUB_REPO}  Branch: {GITHUB_BRANCH}")
    print(f"  File : {GITHUB_FILE}")
    print("═"*62)

    print(f"\n[1/5] Descargando lista...")
    r = requests.get(RAW_URL, timeout=15)
    r.raise_for_status()
    canales = parsear_m3u(r.text)
    print(f"      {len(canales)} canales.")

    print(f"\n[2/5] Validando streams...")
    canales = validar_todos(canales)
    caidos  = [c for c in canales if c["estado"] != "activo"]
    print(f"\n      ✅ {len(canales)-len(caidos)} activos  ❌ {len(caidos)} caídos")

    n_sust = n_sin = 0
    if caidos:
        print(f"\n[3/5] Construyendo pool de sustitutos...")
        pool = construir_pool()
        print(f"\n[4/5] Buscando sustitutos...\n")
        for c in caidos:
            print(f"  → {c['nombre']}")
            res = buscar_sustituto(c, pool)
            if res:
                print(f"     ✅ {res['nombre_encontrado']} (score={res['score']:.2f}, {res['ms']}ms)")
                c.update({"url": res["url"], "estado": "activo", "sustituido": True,
                           "sustituto_fuente": res["fuente"], "ms": res["ms"]})
                n_sust += 1
            else:
                print(f"     ❌ Sin sustituto.")
                n_sin += 1
    else:
        print(f"\n[3/5] Todos activos, sin sustituciones necesarias.")

    contenido = reconstruir_m3u(canales)
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write(contenido)
    print(f"\n[4/5] Lista guardada localmente → {OUTPUT_M3U}")

    print(f"\n[5/5] Subiendo a GitHub...")
    actualizar_github(contenido)
    guardar_html(canales, OUTPUT_HTML)

    print("\n" + "═"*62)
    print(f"  ✅ Activos: {len(canales)-len(caidos)}  ⟳ Sustituidos: {n_sust}  ❌ Sin sub: {n_sin}  📋 Total: {len(canales)}")
    print("═"*62)

if __name__ == "__main__":
    main()
