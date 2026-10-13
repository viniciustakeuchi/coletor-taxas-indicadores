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
    decodificar_csv,
    interpretar_csv_tesouro,
    interpretar_json_tesouro,
    normalizar_titulo,
)
from coletor.retry import PoliticaRetry
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


def test_interpreta_json_do_site() -> None:
    cotacoes, rejeitados = interpretar_json_tesouro(fixture_json("tesouro_titulos.json"))
    assert rejeitados == 1  # o item sem vencimento
    assert len(cotacoes) == 4
    prefixado = cotacoes[0]
    assert prefixado.titulo == "Tesouro Prefixado"
    assert prefixado.vencimento == date(2028, 1, 1)
    assert prefixado.data_base == date(2025, 10, 7)
    assert prefixado.taxa_compra == Decimal("13.42")
    assert prefixado.pu_venda == Decimal("732.15")


def test_titulo_fora_de_venda_fica_sem_taxa_de_compra() -> None:
    cotacoes, _ = interpretar_json_tesouro(fixture_json("tesouro_titulos.json"))
    semestral = next(c for c in cotacoes if c.vencimento == date(2026, 8, 15))
    assert semestral.taxa_compra is None
    assert semestral.pu_compra is None
    assert semestral.taxa_venda == Decimal("9.85")


@pytest.mark.parametrize("payload", [{}, {"response": {}}, [], None, {"response": {"x": 1}}])
def test_json_sem_estrutura_esperada(payload: Any) -> None:
    with pytest.raises(FormatoInesperado):
        interpretar_json_tesouro(payload)


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


# ---------------------------------------------------------- estratégia e contingência


def fonte(capturar: Any, baixar: Any, somente_csv: bool = False) -> FonteTesouro:
    return FonteTesouro(capturar, baixar, SEM_ESPERA, somente_csv=somente_csv)


async def csv_fixture() -> str:
    return fixture_texto("tesouro_precos_taxas.csv")


async def test_usa_o_site_quando_ele_responde() -> None:
    async def capturar() -> Any:
        return fixture_json("tesouro_titulos.json")

    coleta = await fonte(capturar, csv_fixture).coletar(date(2025, 10, 2), date(2025, 10, 7))
    assert coleta.estrategia == "site"
    assert coleta.tentativas == 1
    assert len(coleta.registros) == 4


async def test_cai_para_o_csv_quando_o_site_falha() -> None:
    chamadas = 0

    async def capturar() -> Any:
        nonlocal chamadas
        chamadas += 1
        raise ErroPlaywright("Timeout 30000ms exceeded")

    coleta = await fonte(capturar, csv_fixture).coletar(date(2025, 10, 2), date(2025, 10, 7))
    assert chamadas == 2  # esgotou as tentativas no site
    assert coleta.estrategia == "csv"
    assert coleta.tentativas == 3  # 2 no site + 1 no CSV
    assert len(coleta.registros) == 8


async def test_cai_para_o_csv_quando_o_json_do_site_muda() -> None:
    async def capturar() -> Any:
        return {"formato": "novo"}

    coleta = await fonte(capturar, csv_fixture).coletar(date(2025, 10, 2), date(2025, 10, 7))
    assert coleta.estrategia == "csv"
    assert coleta.tentativas == 2  # formato errado não é repetido


async def test_modo_historico_nao_abre_o_navegador() -> None:
    async def capturar() -> Any:
        raise AssertionError("não deveria abrir o site")

    f = fonte(capturar, csv_fixture, somente_csv=True)
    coleta = await f.coletar(date(2024, 1, 1), date(2025, 12, 31))
    assert f.nome == "tesouro_historico"
    assert coleta.estrategia == "csv"
    assert len(coleta.registros) == 9
