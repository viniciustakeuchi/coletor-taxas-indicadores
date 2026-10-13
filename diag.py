import asyncio, json
from playwright.async_api import async_playwright
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        ctx = await b.new_context(locale="pt-BR", user_agent=UA)
        p = await ctx.new_page()
        corpos = {}
        async def on(r):
            if "/o/rentabilidade" in r.url or "/o/c/" in r.url:
                try:
                    corpos[r.url] = await r.text()
                except Exception as e:
                    corpos[r.url] = repr(e)
        p.on("response", on)
        url = "https://www.tesourodireto.com.br/produtos/dados-sobre-titulos/rendimento-dos-titulos"
        r = await p.goto(url, wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(10000)
        # clicar em abas de resgate, se houver
        for texto in ["Resgatar", "resgatar", "Vender"]:
            try:
                await p.get_by_text(texto, exact=False).first.click(timeout=3000)
                await p.wait_for_timeout(4000)
                print("CLICOU", texto)
                break
            except Exception as e:
                pass
        for u, c in corpos.items():
            print("=====", u, len(c))
            print(c[:3500])
        await b.close()
asyncio.run(main())
