"""Espiona as chamadas de rede do site da Bolsa de Aposta para descobrir os endpoints de dados."""
import json, sys, time
from playwright.sync_api import sync_playwright

OUT = r"C:\Users\BARBARA DE PAULA\betbot\tmp\net_log.jsonl"
seen = []

def log_req(req):
    url = req.url
    low = url.lower()
    # ignora estaticos obvios
    if any(x in low for x in ['.png', '.jpg', '.svg', '.woff', '.css', '.ico', 'gtm', 'google-analytics', 'googletagmanager', 'zdassets', 'legitimuz']):
        return
    seen.append({"type": req.resource_type, "method": req.method, "url": url})

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        locale="pt-BR",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.new_page()
    page.on("request", log_req)
    try:
        page.goto("https://bolsadeaposta.bet.br/b/exchange", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("goto warn:", e)
    # deixa o app carregar os dados (websocket/xhr)
    page.wait_for_timeout(15000)
    # salva texto visivel da pagina para entender o layout
    try:
        body = page.inner_text("body")
        with open(r"C:\Users\BARBARA DE PAULA\betbot\tmp\page_text.txt", "w", encoding="utf-8") as f:
            f.write(body)
        print("PAGE TEXT LEN:", len(body))
    except Exception as e:
        print("text err:", e)
    browser.close()

with open(OUT, "w", encoding="utf-8") as f:
    for s in seen:
        f.write(json.dumps(s) + "\n")
print("REQS:", len(seen))
for s in seen[:60]:
    print(s["method"], s["type"], s["url"][:160])
