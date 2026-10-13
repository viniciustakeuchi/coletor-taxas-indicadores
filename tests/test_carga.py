from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import coletor.carga
from coletor.carga import gravar_indicadores, gravar_taxas_tesouro
from coletor.fontes.base import CotacaoTesouro, PontoIndicador
from coletor.fontes.sgs import interpretar_sgs
from coletor.fontes.tesouro import interpretar_csv_tesouro
from coletor.models import Indicador, TaxaTesouro
from tests.conftest import fixture_json, fixture_texto


async def contar(sessao: AsyncSession, modelo: type[Indicador] | type[TaxaTesouro]) -> int:
    return (await sessao.scalar(select(func.count()).select_from(modelo))) or 0


async def test_primeira_carga_insere_tudo(sessao: AsyncSession) -> None:
    pontos, _ = interpretar_sgs("cdi", fixture_json("sgs_cdi.json"))
    assert await gravar_indicadores(sessao, pontos) == 5
    await sessao.commit()
    assert await contar(sessao, Indicador) == 5


async def test_rodar_duas_vezes_nao_duplica_nem_reescreve(sessao: AsyncSession) -> None:
    pontos, _ = interpretar_sgs("cdi", fixture_json("sgs_cdi.json"))
    await gravar_indicadores(sessao, pontos)
    await sessao.commit()

    assert await gravar_indicadores(sessao, pontos) == 0
    await sessao.commit()
    assert await contar(sessao, Indicador) == 5


async def test_valor_revisado_pela_fonte_e_atualizado(sessao: AsyncSession) -> None:
    original = PontoIndicador("ipca", date(2025, 9, 1), Decimal("0.48"))
    await gravar_indicadores(sessao, [original])
    await sessao.commit()

    revisado = PontoIndicador("ipca", date(2025, 9, 1), Decimal("0.50"))
    novo = PontoIndicador("ipca", date(2025, 10, 1), Decimal("0.09"))
    assert await gravar_indicadores(sessao, [revisado, novo]) == 2
    await sessao.commit()

    valor = await sessao.scalar(select(Indicador.valor).where(Indicador.data == date(2025, 9, 1)))
    assert valor == Decimal("0.50")


async def test_chave_repetida_no_mesmo_lote_fica_com_a_ultima(sessao: AsyncSession) -> None:
    pontos = [
        PontoIndicador("cdi", date(2025, 10, 1), Decimal("0.05")),
        PontoIndicador("cdi", date(2025, 10, 1), Decimal("0.06")),
    ]
    assert await gravar_indicadores(sessao, pontos) == 1
    await sessao.commit()
    assert await sessao.scalar(select(Indicador.valor)) == Decimal("0.06")


async def test_lista_vazia_nao_toca_no_banco(sessao: AsyncSession) -> None:
    assert await gravar_indicadores(sessao, []) == 0


async def test_taxas_do_tesouro_sao_idempotentes_e_aceitam_nulos(sessao: AsyncSession) -> None:
    cotacoes, _ = interpretar_csv_tesouro(fixture_texto("tesouro_precos_taxas.csv").splitlines())
    assert await gravar_taxas_tesouro(sessao, cotacoes) == 9
    await sessao.commit()
    assert await gravar_taxas_tesouro(sessao, cotacoes) == 0
    await sessao.commit()

    semestral = await sessao.scalar(
        select(TaxaTesouro).where(
            TaxaTesouro.vencimento == date(2026, 8, 15),
            TaxaTesouro.data_base == date(2025, 10, 7),
        )
    )
    assert semestral is not None
    assert semestral.taxa_compra is None
    assert semestral.taxa_venda == Decimal("9.8500")


async def test_carga_em_lotes(sessao: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coletor.carga, "TAMANHO_LOTE", 3)
    cotacoes = [
        CotacaoTesouro("Tesouro Selic", date(2029, 3, 1), date(2025, 1, d), *[None] * 4)
        for d in range(1, 11)
    ]
    assert await gravar_taxas_tesouro(sessao, cotacoes) == 10
    await sessao.commit()
    assert await contar(sessao, TaxaTesouro) == 10
