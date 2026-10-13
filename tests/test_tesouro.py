from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from playwright.async_api import Error as ErroPlaywright

from coletor.fontes.base import FormatoInesperado
from coletor.fontes.tesouro import (
    FonteTesouro,
    baixar_csv_tesouro,
    baixar_json_tesouro,
    decodificar_csv,
    interpretar_csv_tesouro,
    interpretar_json_tesouro,
    normalizar_titulo,
    taxa_do_texto,
)
from coletor.retry import PoliticaRetry, erro_transitorio_http
from tests.conftest import fixture_json, fixture_texto

SEM_ESPERA = PoliticaRetry(tentativas=2, backoff_inicial=0, backoff_maximo=0)


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Tesouro Prefixado 2028", "Tesouro Prefixado"),
        ("Tesouro IPCA+ com Juros Semestrais 2035", "Tesouro IPCA+ com Juros Semestrais"),
        ("  Tesouro   Selic 2029 ", "Tesouro Selic"),
        ("Tesouro Selic", "Tesouro Selic"),
    ],
)
def test_normaliza_nome_do_titulo(nome: str, esperado: str) -> None:
    assert normalizar_titulo(nome) == esperado


def test_interpreta_json_de_rentabilidade_do_site() -> None:
    cotacoes, rejeitados = interpretar_json_tesouro(fixture_json("tesouro_rentabilidade.json"))
    assert rejeitados == 0
    assert len(cotacoes) == 9  # 8 do Tesouro Direto e o Tesouro Reserva (24x7)
    prefixado = next(c for c in cotacoes if c.vencimento == date(2029, 1, 1))
    assert prefixado.titulo == "Tesouro Prefixado"
    assert prefixado.data_base == date(2026, 10, 13)
    assert prefixado.taxa_compra == Decimal("12.45")
    assert prefixado.taxa_venda == Decimal("12.57")
    assert prefixado.pu_compra == Decimal("772.98")
    assert prefixado.pu_venda == Decimal("771.18")


def test_titulo_fora_de_venda_fica_sem_taxa_e_preco_de_compra() -> None:
    cotacoes, _ = interpretar_json_tesouro(fixture_json("tesouro_rentabilidade.json"))
    selic_2027 = next(c for c in cotacoes if c.vencimento == date(2027, 3, 1))
    assert selic_2027.taxa_compra is None
    assert selic_2027.pu_compra is None
    assert selic_2027.taxa_venda == Decimal("0.0218")


def test_renda_mais_usa_o_vencimento_e_nao_o_ano_do_nome() -> None:
    cotacoes, _ = interpretar_json_tesouro(fixture_json("tesouro_rentabilidade.json"))
    renda = next(c for c in cotacoes if c.titulo == "Tesouro Renda+ Aposentadoria Extra")
    assert renda.vencimento == date(2049, 12, 15)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("IPCA + 6,82%", Decimal("6.82")),
        ("SELIC + 0,0983%", Decimal("0.0983")),
        ("12,57%", Decimal("12.57")),
        ("IGP-M + 7,85%", Decimal("7.85")),
        ("prefixado", None),
        ("SELIC", None),
        ("", None),
        (None, None),
    ],
)
def test_taxa_extraida_do_texto(texto: str | None, esperado: Decimal | None) -> None:
    assert taxa_do_texto(texto) == esperado


def test_item_malformado_e_contado_e_descartado() -> None:
    payload = fixture_json("tesouro_rentabilidade.json")
    payload["TesouroLegado"].append({"treasuryBondName": "Título sem vencimento"})
    payload["TesouroLegado"].append({**payload["TesouroLegado"][0], "maturityDate": "amanhã"})
    cotacoes, rejeitados = interpretar_json_tesouro(payload)
    assert (len(cotacoes), rejeitados) == (9, 2)


@pytest.mark.parametrize(
    "payload",
    [{}, [], None, {"TesouroLegado": []}, {"response": {"TrsrBdTradgList": []}}, {"x": [1, 2]}],
)
def test_json_sem_estrutura_esperada(payload: Any) -> None:
    with pytest.raises(FormatoInesperado):
        interpretar_json_tesouro(payload)


async def test_baixa_json_da_api() -> None:
    def responder(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/o/rentabilidade/resgatar"
        assert "Mozilla" in req.headers["User-Agent"]
        return httpx.Response(200, json=fixture_json("tesouro_rentabilidade.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as cliente:
        payload = await baixar_json_tesouro(cliente, "https://td.exemplo/o/rentabilidade/resgatar")
    assert "TesouroLegado" in payload


async def test_api_que_responde_html_vira_formato_inesperado() -> None:
    transporte = httpx.MockTransport(lambda req: httpx.Response(200, text="<html>"))
    async with httpx.AsyncClient(transport=transporte) as cliente:
        with pytest.raises(FormatoInesperado):
            await baixar_json_tesouro(cliente, "https://td.exemplo/o/rentabilidade/resgatar")


async def test_endpoint_desativado_responde_410_e_nao_e_repetido() -> None:
    transporte = httpx.MockTransport(lambda req: httpx.Response(410))
    async with httpx.AsyncClient(transport=transporte) as cliente:
        with pytest.raises(httpx.HTTPStatusError) as erro:
            await baixar_json_tesouro(cliente, "https://td.exemplo/json/antigo.json")
    assert not erro_transitorio_http(erro.value)


def test_interpreta_csv_do_tesouro_transparente() -> None:
    cotacoes, rejeitados = interpretar_csv_tesouro(
        fixture_texto("tesouro_precos_taxas.csv").splitlines()
    )
    assert rejeitados == 1  # vencimento inválido
    assert len(cotacoes) == 9
    selic = next(
        c for c in cotacoes if c.titulo == "Tesouro Selic" and c.data_base == date(2025, 10, 7)
    )
    assert selic.taxa_compra == Decimal("0.0598")
    assert selic.pu_compra == Decimal("17103.12")


def test_csv_filtra_pelo_periodo() -> None:
    cotacoes, _ = interpretar_csv_tesouro(
        fixture_texto("tesouro_precos_taxas.csv").splitlines(),
        desde=date(2025, 10, 7),
        ate=date(2025, 10, 7),
    )
    assert {c.data_base for c in cotacoes} == {date(2025, 10, 7)}
    assert len(cotacoes) == 4


def test_csv_com_colunas_em_outra_ordem() -> None:
    linhas = [
        "Data Base;Tipo Titulo;Data Vencimento;PU Venda Manha;PU Compra Manha;"
        "Taxa Venda Manha;Taxa Compra Manha",
        "07/10/2025;Tesouro Selic;01/03/2029;17097,40;17103,12;0,0698;0,0598",
    ]
    [cotacao], _ = interpretar_csv_tesouro(linhas)
    assert cotacao.pu_venda == Decimal("17097.40")
    assert cotacao.taxa_compra == Decimal("0.0598")


def test_csv_com_cabecalho_diferente_quebra_com_erro_claro() -> None:
    with pytest.raises(FormatoInesperado):
        interpretar_csv_tesouro(["Titulo;Vencimento;Taxa", "x;y;z"])


def test_csv_vazio() -> None:
    with pytest.raises(FormatoInesperado):
        interpretar_csv_tesouro([])


def test_decodifica_csv_utf8_com_bom_e_latin1() -> None:
    assert decodificar_csv("﻿Tipo Título".encode()) == "Tipo Título"
    assert decodificar_csv("Tipo Título".encode("latin-1")) == "Tipo Título"


async def test_baixa_csv_seguindo_redirecionamento() -> None:
    def responder(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/download":
            return httpx.Response(302, headers={"Location": "/arquivo.csv"})
        return httpx.Response(200, content=fixture_texto("tesouro_precos_taxas.csv").encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as cliente:
        texto = await baixar_csv_tesouro(cliente, "https://tesouro.exemplo/download")
    assert texto.startswith("Tipo Titulo;")


# ---------------------------------------------------------- estratégias em camadas


def site_ok() -> Any:
    async def operacao() -> Any:
        return fixture_json("tesouro_rentabilidade.json")

    return operacao


def falha(erro: Exception, chamadas: list[str], nome: str) -> Any:
    async def operacao() -> Any:
        chamadas.append(nome)
        raise erro

    return operacao


async def csv_fixture() -> str:
    return fixture_texto("tesouro_precos_taxas.csv")


def fonte(
    api: Any, navegador: Any, csv: Any = csv_fixture, somente_csv: bool = False
) -> FonteTesouro:
    return FonteTesouro(api, navegador, csv, SEM_ESPERA, somente_csv=somente_csv)


PERIODO = (date(2025, 10, 2), date(2025, 10, 7))


async def test_usa_a_api_quando_ela_responde() -> None:
    chamadas: list[str] = []
    f = fonte(site_ok(), falha(AssertionError(), chamadas, "navegador"))
    coleta = await f.coletar(*PERIODO)
    assert coleta.estrategia == "api"
    assert coleta.tentativas == 1
    assert len(coleta.registros) == 9
    assert chamadas == []


async def test_usa_o_navegador_quando_a_api_sai_do_ar() -> None:
    chamadas: list[str] = []
    erro_410 = httpx.HTTPStatusError(
        "gone",
        request=httpx.Request("GET", "https://td"),
        response=httpx.Response(410, request=httpx.Request("GET", "https://td")),
    )
    coleta = await fonte(falha(erro_410, chamadas, "api"), site_ok()).coletar(*PERIODO)
    assert chamadas == ["api"]  # 410 não é repetido
    assert coleta.estrategia == "navegador"
    assert coleta.tentativas == 2


async def test_cai_para_o_csv_quando_api_e_navegador_falham() -> None:
    chamadas: list[str] = []
    f = fonte(
        falha(httpx.ConnectError("caiu"), chamadas, "api"),
        falha(ErroPlaywright("Timeout 30000ms exceeded"), chamadas, "navegador"),
    )
    coleta = await f.coletar(*PERIODO)
    assert chamadas == ["api", "api", "navegador", "navegador"]  # 2 tentativas cada
    assert coleta.estrategia == "csv"
    assert coleta.tentativas == 5
    assert len(coleta.registros) == 8


async def test_formato_novo_na_api_passa_para_a_proxima_camada() -> None:
    async def formato_novo() -> Any:
        return {"formato": "novo"}

    coleta = await fonte(formato_novo, site_ok()).coletar(*PERIODO)
    assert coleta.estrategia == "navegador"
    assert coleta.tentativas == 2  # formato errado não é repetido


async def test_modo_historico_vai_direto_ao_csv() -> None:
    chamadas: list[str] = []
    f = fonte(
        falha(AssertionError(), chamadas, "api"),
        falha(AssertionError(), chamadas, "navegador"),
        somente_csv=True,
    )
    coleta = await f.coletar(date(2024, 1, 1), date(2025, 12, 31))
    assert f.nome == "tesouro_historico"
    assert coleta.estrategia == "csv"
    assert len(coleta.registros) == 9
    assert chamadas == []
