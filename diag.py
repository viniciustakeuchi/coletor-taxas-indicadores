import asyncio, json
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        ctx = await b.new_context(locale="pt-BR", user_agent=UA)
        p = await ctx.new_page()
        def on(r):
            ct = r.headers.get("content-type","")
            if "json" in ct or "json" in r.url or r.request.resource_type in ("document","xhr","fetch"):
                print("RESP", r.status, r.request.resource_type, ct[:30], r.url[:200], flush=True)
        p.on("response", on)
        for url in ["https://www.tesourodireto.com.br/titulos/precos-e-taxas.htm","https://www.tesourodireto.com.br/produtos/dados-sobre-titulos/rendimento-dos-titulos"]:
            try:
                r = await p.goto(url, wait_until="domcontentloaded", timeout=45000)
                print("GOTO", url, r.status if r else None, p.url, flush=True)
                await p.wait_for_timeout(12000)
                print("TITLE", await p.title())
                html = await p.content()
                print("HTML", len(html), html[:600].replace("\n"," "))
                import re
                print("SCRIPTS", re.findall(r'src="([^"]+\.js[^"]*)"', html)[:30])
                print("JSONREFS", sorted(set(re.findall(r'[\w/\-.]+\.json', html)))[:30])
            except Exception as e:
                print("ERR", url, repr(e)[:300])
        await b.close()
asyncio.run(main())
