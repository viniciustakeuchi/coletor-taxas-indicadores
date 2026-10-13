"""Playwright de verdade (Chromium headless) contra um servidor local que imita o site."""

import pytest
from playwright.async_api import Error as ErroPlaywright

from coletor.fontes.tesouro import capturar_json_tesouro, interpretar_json_tesouro
from tests.conftest import SiteFalso

pytestmark = pytest.mark.navegador


async def test_captura_o_json_que_a_pagina_carrega(site_tesouro: SiteFalso) -> None:
    payload = await capturar_json_tesouro(site_tesouro.url_pagina, timeout_s=15)
    cotacoes, _ = interpretar_json_tesouro(payload)
    assert len(cotacoes) == 9


async def test_bloqueia_imagens_e_css(site_tesouro: SiteFalso) -> None:
    await capturar_json_tesouro(site_tesouro.url_pagina, timeout_s=15)
    assert not any(p.endswith((".png", ".css")) for p in site_tesouro.requisicoes)


async def test_json_com_erro_http_vira_erro_do_navegador(site_tesouro: SiteFalso) -> None:
    site_tesouro.status_json = 503
    with pytest.raises(ErroPlaywright, match="HTTP 503"):
        await capturar_json_tesouro(site_tesouro.url_pagina, timeout_s=15)


async def test_pagina_que_nao_carrega_o_json_estoura_o_timeout(site_tesouro: SiteFalso) -> None:
    site_tesouro.servir_json = False
    site_tesouro.status_json = 404
    with pytest.raises(ErroPlaywright):
        await capturar_json_tesouro(f"{site_tesouro.url}/outra-pagina", timeout_s=2)
