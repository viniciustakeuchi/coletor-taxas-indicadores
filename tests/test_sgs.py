from datetime import date
from decimal import Decimal

import httpx
import pytest

from coletor.fontes.base import FormatoInesperado
from coletor.fontes.sgs import SERIES, FonteSGS, interpretar_sgs, janelas
from coletor.retry import PoliticaRetry
from tests.conftest import fixture_json

SEM_ESPERA = PoliticaRetry(tentativas=3, backoff_inicial=0, backoff_maximo=0)
URL = "https://api.bcb.gov.br/dados/serie"


def test_interpreta_resposta_do_sgs() -> None:
    pontos, rejeitados = interpretar_sgs("cdi", fixture_json("sgs_cdi.json"))
    assert rejeitados == 0
    assert len(pontos) == 5
    assert pontos[0].serie == "cdi"
    assert pontos[0].data == date(2025, 10, 1)
    assert pontos[0].valor == Decimal("0.055131")


def test_valor_negativo_e_aceito() -> None:
    pontos, _ = interpretar_sgs("ipca", fixture_json("sgs_ipca.json"))
    assert Decimal("-0.11") in {p.valor for p in pontos}


def test_itens_malformados_sao_contados_e_descartados() -> None:
    payload = [
        {"data": "01/10/2025", "valor": "0.055131"},
        {"data": "2025-10-02", "valor": "0.055131"},
        {"data": "03/10/2025", "valor": ""},
        {"data": "06/10/2025", "valor": "NaN"},
        {"valor": "1"},
        "lixo",
    ]
    pontos, rejeitados = interpretar_sgs("cdi", payload)
    assert len(pontos) == 1
    assert rejeitados == 5


def test_resposta_que_nao_e_lista_quebra_com_erro_claro() -> None:
    with pytest.raises(FormatoInesperado):
        interpretar_sgs("cdi", {"erro": "Value(s) not found"})


def test_janelas_respeitam_limite_de_10_anos() -> None:
    blocos = janelas(date(2000, 1, 1), date(2025, 12, 31))
    assert blocos[0][0] == date(2000, 1, 1)
    assert blocos[-1][1] == date(2025, 12, 31)
    assert all((fim - inicio).days < 3650 for inicio, fim in blocos)
    for (_, fim), (inicio, _) in zip(blocos, blocos[1:], strict=False):
        assert (inicio - fim).days == 1


def test_janela_curta_e_um_bloco_so() -> None:
    assert janelas(date(2025, 10, 1), date(2025, 10, 7)) == [(date(2025, 10, 1), date(2025, 10, 7))]


def fonte_com(handler: httpx.MockTransport, serie: str = "cdi") -> FonteSGS:
    cliente = httpx.AsyncClient(transport=handler)
    return FonteSGS(SERIES[serie], cliente, SEM_ESPERA, URL)


async def test_coleta_monta_url_e_parametros_do_sgs() -> None:
    requisicoes: list[httpx.Request] = []

    def responder(req: httpx.Request) -> httpx.Response:
        requisicoes.append(req)
        return httpx.Response(200, json=fixture_json("sgs_cdi.json"))

    coleta = await fonte_com(httpx.MockTransport(responder)).coletar(
        date(2025, 10, 1), date(2025, 10, 7)
    )
    assert len(coleta.registros) == 5
    assert coleta.tentativas == 1
    req = requisicoes[0]
    assert req.url.path == "/dados/serie/bcdata.sgs.12/dados"
    assert req.url.params["dataInicial"] == "01/10/2025"
    assert req.url.params["dataFinal"] == "07/10/2025"
    assert req.url.params["formato"] == "json"


async def test_404_do_sgs_significa_periodo_sem_dados() -> None:
    fonte = fonte_com(httpx.MockTransport(lambda req: httpx.Response(404)))
    coleta = await fonte.coletar(date(2025, 10, 4), date(2025, 10, 5))
    assert coleta.registros == []


async def test_erro_5xx_e_repetido_ate_dar_certo() -> None:
    respostas = iter(
        [
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json=fixture_json("sgs_cdi.json")),
        ]
    )
    fonte = fonte_com(httpx.MockTransport(lambda req: next(respostas)))
    coleta = await fonte.coletar(date(2025, 10, 1), date(2025, 10, 7))
    assert coleta.tentativas == 3
    assert len(coleta.registros) == 5


async def test_falha_persistente_propaga_o_erro() -> None:
    fonte = fonte_com(httpx.MockTransport(lambda req: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        await fonte.coletar(date(2025, 10, 1), date(2025, 10, 7))


async def test_resposta_html_vira_formato_inesperado() -> None:
    fonte = fonte_com(
        httpx.MockTransport(lambda req: httpx.Response(200, text="<html>manutenção</html>"))
    )
    with pytest.raises(FormatoInesperado):
        await fonte.coletar(date(2025, 10, 1), date(2025, 10, 7))


async def test_periodo_longo_faz_uma_requisicao_por_janela() -> None:
    chamadas = 0

    def responder(req: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        return httpx.Response(200, json=[])

    await fonte_com(httpx.MockTransport(responder)).coletar(date(2005, 1, 1), date(2025, 1, 1))
    assert chamadas == 3
