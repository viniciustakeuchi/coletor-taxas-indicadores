from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from coletor.carga import gravar_indicadores
from coletor.fontes.base import PontoIndicador
from coletor.fontes.sgs import interpretar_sgs
from coletor.metricas import (
    acumular_mensal,
    anualizar_taxa_diaria,
    calcular_premissas,
    metricas_por_fonte,
    percentil,
)
from coletor.models import Execucao
from tests.conftest import fixture_json

AGORA = datetime(2025, 10, 7, 22, 0, tzinfo=UTC)


def test_percentil_nearest_rank() -> None:
    assert percentil([], 95) == 0
    assert percentil([10], 95) == 10
    assert percentil(list(range(1, 21)), 95) == 19
    assert percentil(list(range(1, 21)), 50) == 10


def test_cdi_diario_anualizado_em_252_dias() -> None:
    assert anualizar_taxa_diaria(Decimal("0.055131")) == Decimal("14.90")


def test_ipca_acumulado_e_composto() -> None:
    assert acumular_mensal([Decimal("1"), Decimal("1")]) == Decimal("2.01")


def execucao(fonte: str, status: str, ms: int, dias_atras: int = 0, **extra: object) -> Execucao:
    return Execucao(
        fonte=fonte,
        status=status,
        iniciada_em=AGORA - timedelta(days=dias_atras, minutes=ms),
        duracao_ms=ms,
        tentativas=1,
        registros_lidos=0,
        registros_rejeitados=0,
        registros_gravados=extra.get("gravados", 0),
        estrategia=extra.get("estrategia"),
    )


async def test_metricas_por_fonte(sessao: AsyncSession) -> None:
    sessao.add_all(
        [
            execucao("cdi", "sucesso", 300, gravados=5, estrategia="api"),
            execucao("cdi", "sucesso", 500, gravados=1, estrategia="api"),
            execucao("cdi", "falha", 900),
            execucao("cdi", "sucesso", 400, gravados=1, estrategia="api"),
            execucao("tesouro", "sucesso", 4000, estrategia="site"),
            execucao("tesouro", "sucesso", 6000, estrategia="csv"),
            execucao("cdi", "sucesso", 100, dias_atras=60),  # fora da janela
            execucao("ipca", "em_andamento", 0),  # ainda rodando
        ]
    )
    await sessao.commit()

    metricas = {m.fonte: m for m in await metricas_por_fonte(sessao, dias=30, agora=AGORA)}
    assert set(metricas) == {"cdi", "tesouro"}
    cdi = metricas["cdi"]
    assert cdi.execucoes == 4
    assert cdi.sucessos == 3
    assert cdi.taxa_sucesso == 0.75
    assert cdi.duracao_media_ms == 525
    assert cdi.duracao_p95_ms == 900
    assert cdi.registros_gravados == 7
    assert cdi.estrategias == {"api": 3}
    assert metricas["tesouro"].estrategias == {"site": 1, "csv": 1}


async def test_premissas_a_partir_das_series(sessao: AsyncSession) -> None:
    cdi, _ = interpretar_sgs("cdi", fixture_json("sgs_cdi.json"))
    ipca, _ = interpretar_sgs("ipca", fixture_json("sgs_ipca.json"))
    meta = [PontoIndicador("selic_meta", date(2025, 10, 7), Decimal("15.00000000"))]
    await gravar_indicadores(sessao, [*cdi, *ipca, *meta])
    await sessao.commit()

    premissas = await calcular_premissas(sessao)
    assert premissas.cdi is not None
    assert premissas.cdi.valor == Decimal("14.90")
    assert premissas.cdi.data_referencia == date(2025, 10, 7)
    assert premissas.selic is not None and premissas.selic.valor == Decimal("15.00")
    # 12 últimos meses (out/2024 a set/2025), sem o ponto de set/2024
    assert premissas.ipca is not None
    assert premissas.ipca.valor == acumular_mensal([p.valor for p in ipca[1:]])
    assert premissas.ipca.data_referencia == date(2025, 9, 1)


async def test_premissas_sem_dados(sessao: AsyncSession) -> None:
    premissas = await calcular_premissas(sessao)
    assert (premissas.cdi, premissas.selic, premissas.ipca) == (None, None, None)
