#!/usr/bin/env python3
"""yt_live_viewer.py — mantém sessões de reprodução numa live do YouTube.

Sem lista de proxies: usa o IP da máquina com contextos de browser isolados
(UA, locale, timezone e cookies próprios por sessão).

Uso:
  python yt_live_viewer.py --viewers 5 --minutes 10
  python yt_live_viewer.py --url https://www.youtube.com/watch?v=ID --viewers 3 --minutes 5 --headless
"""
import argparse
import random
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright nao instalado: python -m pip install playwright && python -m playwright install chrome")

DEFAULT_URL = "https://www.youtube.com/watch?v=8uwd5O8Q9l4"
DEBUG = False

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]
LOCALES = ["pt-BR", "en-US", "es-ES", "fr-FR"]
TZS = ["America/Sao_Paulo", "America/New_York", "Europe/London", "America/Los_Angeles"]
SIZES = [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]


def video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|embed/|live/)([\w-]{11})", url)
    if not m:
        raise SystemExit("ID de video nao encontrado na URL")
    return m.group(1)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def carregar_proxies(caminho, limite=400):
    vistos, out = set(), []
    with open(caminho) as f:
        for linha in f:
            p = linha.strip()
            if not p:
                continue
            # formato Webshare: ip:porta:usuario:senha
            partes = p.split(":")
            if len(partes) == 4 and partes[0].count(".") == 3:
                ip, porta, user, pw = partes
                p = {"server": f"http://{ip}:{porta}", "username": user, "password": pw}
                if p["server"] not in vistos:
                    vistos.add(p["server"])
                    out.append(p)
                if len(out) >= limite:
                    break
                continue
            if "://" not in p:
                p = "http://" + p
            if p.startswith("socks4://"):
                continue  # Chromium/Playwright nao suporta socks4
            if p not in vistos:
                vistos.add(p)
                out.append(p)
            if len(out) >= limite:
                break
    return out


def webshare_proxies(token):
    """Puxa os 10 proxies gratis do Webshare com API token."""
    r = requests.get("https://proxy.webshare.io/api/v2/proxy/list/",
                     params={"page": 1, "page_size": 100},
                     headers={"Authorization": "Token " + token}, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Webshare API {r.status_code}: {r.text[:200]}")
    out = []
    for x in r.json().get("results", []):
        if not x.get("valid") or not x.get("proxy_address"):
            continue
        out.append({
            "server": f"http://{x['proxy_address']}:{x['port']}",
            "username": x.get("username", ""),
            "password": x.get("password", ""),
        })
    return out


def desc_proxy(p):
    if isinstance(p, dict):
        return p.get("server", "?")
    return str(p)


def testar_proxy(p):
    """Teste rapido: TCP do proxy + GET no YouTube atraves dele."""
    from urllib.parse import urlparse
    if isinstance(p, dict):
        server = p["server"]
        user, pw = p.get("username", ""), p.get("password", "")
        if user:
            u = urlparse(server)
            req_proxy = f"http://{user}:{pw}@{u.hostname}:{u.port}"
        else:
            req_proxy = server
    else:
        server = req_proxy = p
    try:
        u = urlparse(server)
        with socket.create_connection((u.hostname, u.port or 80), timeout=4):
            pass
    except Exception:
        return None
    if not server.startswith(("http://", "https://")):
        return p  # socks: so TCP; o Playwright testa de verdade
    try:
        r = requests.get("https://www.youtube.com/generate_204",
                         proxies={"http": req_proxy, "https": req_proxy}, timeout=7)
        return p if r.status_code in (200, 204) else None
    except Exception:
        return None


def proxies_ativos(lista, max_workers=40):
    bons = []
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        fut = {exe.submit(testar_proxy, p): p for p in lista}
        for f in as_completed(fut):
            try:
                r = f.result()
            except Exception:
                r = None
            if r:
                bons.append(r)
    return bons


def verificar_no_browser(proxies, vid, limite=12, timeout=12000):
    """Teste real: so aprova proxy que consegue reproduzir a live no Chromium."""
    bons = []
    url = f"https://www.youtube.com/watch?v={vid}&autoplay=1&mute=1"
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                               args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        for p in proxies[:limite]:
            ctx = None
            try:
                ctx = b.new_context(proxy=(p if isinstance(p, dict) else {"server": p}))
                pg = ctx.new_page()
                pg.goto(url, wait_until="domcontentloaded", timeout=timeout)
                pg.wait_for_function(
                    "() => {const v=document.querySelector('video'); return v && v.currentTime>0.15 && !v.paused;}",
                    timeout=timeout)
                bons.append(p)
                log("  proxy OK: " + p)
            except Exception:
                pass
            finally:
                if ctx:
                    try:
                        ctx.close()
                    except Exception:
                        pass
        b.close()
    return bons


def make_page(browser, vid, idx, proxy=None):
    ctx = browser.new_context(
        user_agent=random.choice(UAS),
        viewport={"width": random.choice(SIZES)[0], "height": random.choice(SIZES)[1]},
        locale=random.choice(LOCALES),
        timezone_id=random.choice(TZS),
        device_scale_factor=1,
        proxy=(proxy if isinstance(proxy, dict) else ({"server": proxy} if proxy else None)),
    )
    ctx.add_cookies([{
        "name": "CONSENT", "value": "YES+cb.20210720-07-p0.en+FX+123",
        "domain": ".youtube.com", "path": "/",
    }])
    page = ctx.new_page()
    # pagina de watch (o embed desta live esta desativado: erro 152)
    url = f"https://www.youtube.com/watch?v={vid}&autoplay=1&mute=1&hl=pt-BR"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=35000)
        page.evaluate("() => { const v=document.querySelector('video'); if(v){v.muted=true;v.play().catch(()=>{});} }")
        if DEBUG:
            page.wait_for_timeout(9000)
            log(f"DEBUG {idx} title={page.title()}")
            log("DEBUG body=" + page.locator("body").inner_text()[:700].replace("\n", " | "))
            log("DEBUG video=" + str(page.evaluate(
                "()=>{const v=document.querySelector('video');return v?{t:v.currentTime,paused:v.paused,ready:v.readyState,src:(v.currentSrc||'').slice(0,60),err:v.error&&v.error.message}:null}")))
        page.wait_for_function(
            "() => { const v = document.querySelector('video'); return v && v.currentTime > 0.15 && !v.paused; }",
            timeout=30000,
        )
        return ctx, page, True
    except Exception as e:
        log(f"viewer {idx}: falha ao iniciar ({str(e)[:90]})")
        return ctx, page, False


def state(page):
    try:
        return page.evaluate("""() => {
            const v = document.querySelector('video');
            if (!v) return 'no-video';
            if (v.ended) return 'ended';
            if (v.paused) return 'paused';
            return 'playing t=' + Math.floor(v.currentTime);
        }""")
    except Exception:
        return 'erro'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--viewers", type=int, default=5)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--chrome", action="store_true", help="usa o Google Chrome instalado")
    ap.add_argument("--debug-page", action="store_true", help="imprime estado da pagina/player")
    ap.add_argument("--check-every", type=int, default=20, help="segundos entre checagens")
    ap.add_argument("--proxy-file", help="arquivo com proxies (um por linha)")
    ap.add_argument("--webshare-token", help="API token do Webshare (plano free: 10 proxies)")
    ap.add_argument("--proxy-limit", type=int, default=200, help="quantos proxies testar")
    ap.add_argument("--verify-proxies", action="store_true",
                    help="testa cada proxy no browser antes de usar (mais lento, mais preciso)")
    a = ap.parse_args()
    global DEBUG
    DEBUG = a.debug_page

    vid = video_id(a.url)
    proxies = []
    if a.webshare_token:
        proxies = webshare_proxies(a.webshare_token)
        log(f"Webshare: {len(proxies)} proxies válidos")
        if not proxies:
            sys.exit("Webshare nao devolveu proxies ativos")
    elif a.proxy_file:
        candidatos = carregar_proxies(a.proxy_file, a.proxy_limit)
        log(f"testando {len(candidatos)} proxies...")
        proxies = proxies_ativos(candidatos)
        log(f"proxies ativos: {len(proxies)}")
        if not proxies:
            sys.exit("nenhum proxy respondeu; rode sem --proxy-file")
    if a.verify_proxies and proxies:
        log("verificando reprodução no browser...")
        proxies = verificar_no_browser(proxies, vid)
        log(f"proxies verificados: {len(proxies)}")
    log(f"alvo: {a.url} (id {vid}) | viewers={a.viewers} | duracao={a.minutes}min")

    with sync_playwright() as p:
        launch = {"headless": a.headless,
                  "args": ["--autoplay-policy=no-user-gesture-required",
                           "--mute-audio", "--disable-background-timer-throttling",
                           "--disable-renderer-backgrounding"]}
        if a.chrome:
            launch["channel"] = "chrome"
        browser = p.chromium.launch(**launch)
        viewers = []
        for i in range(a.viewers):
            px = random.choice(proxies) if proxies else None
            ctx, page, ok = make_page(browser, vid, i, px)
            viewers.append({"ctx": ctx, "page": page, "ok": ok, "started": time.time(),
                            "proxy": px})
            log(f"viewer {i}: {'assistindo' if ok else 'inativo'} | {desc_proxy(px) if px else 'direto'}")
            if i + 1 < a.viewers:
                time.sleep(random.uniform(4, 9))

        fim = time.time() + a.minutes * 60
        try:
            while time.time() < fim:
                time.sleep(a.check_every)
                ativos = 0
                for i, v in enumerate(viewers):
                    st = state(v["page"])
                    if st.startswith("playing"):
                        t_atual = int(st.split("t=")[-1]) if "t=" in st else 0
                        if t_atual <= v.get("last_t", -1) + 1:
                            log(f"viewer {i}: player travado em t={t_atual} -> reconectando")
                            st = "travado"
                        v["last_t"] = t_atual
                    if st.startswith("playing"):
                        ativos += 1
                    elif v["ok"]:
                        log(f"viewer {i}: {st} -> reconectando")
                        try:
                            v["page"].reload(wait_until="domcontentloaded", timeout=30000)
                            v["page"].evaluate("() => { const v=document.querySelector('video'); if(v){v.muted=true;v.play().catch(()=>{});} }")
                            v["page"].wait_for_function(
                                "() => { const v=document.querySelector('video'); return v && v.currentTime>0.15 && !v.paused; }",
                                timeout=25000)
                            v["last_t"] = -1
                        except Exception as e:
                            log(f"viewer {i}: reconexao falhou ({str(e)[:70]})")
                            v["ok"] = False
                    if st.startswith("playing"):
                        log(f"  viewer {i}: {st}")
                log(f"ativos: {ativos}/{len(viewers)}")
        except KeyboardInterrupt:
            log("interrompido")
        finally:
            for v in viewers:
                try:
                    v["ctx"].close()
                except Exception:
                    pass
            browser.close()
            log("encerrado")


if __name__ == "__main__":
    main()
