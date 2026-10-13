"""Métricas das execuções e premissas de mercado calculadas a partir das séries coletadas."""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coletor.models import Execucao, Indicador

DIAS_UTEIS_ANO = 252
CENTAVO_PERCENTUAL = Decimal("0.01")


@dataclass(frozen=True)
class MetricaFonte:
    fonte: str
    execucoes: int
    sucessos: int
    taxa_sucesso: float
    duracao_media_ms: int
    duracao_p95_ms: int
    registros_gravados: int
    ultima_execucao: datetime
    ultimo_status: str
    estrategias: dict[str, int]


def percentil(valores: list[int], p: float) -> int:
    """Percentil pelo método nearest-rank."""
    if not valores:
        return 0
    ordenados = sorted(valores)
    return ordenados[max(0, math.ceil(p / 100 * len(ordenados)) - 1)]


async def metricas_por_fonte(
    sessao: AsyncSession, dias: int = 30, agora: datetime | None = None
) -> list[MetricaFonte]:
    agora = agora or datetime.now(UTC)
    consulta = (
        select(Execucao)
        .where(Execucao.iniciada_em >= agora - timedelta(days=dias))
        .where(Execucao.status != "em_andamento")
        .order_by(Execucao.fonte, Execucao.iniciada_em)
    )
    por_fonte: dict[str, list[Execucao]] = {}
    for execucao in (await sessao.scalars(consulta)).all():
        por_fonte.setdefault(execucao.fonte, []).append(execucao)

    metricas = []
    for fonte, execucoes in sorted(por_fonte.items()):
        sucessos = [e for e in execucoes if e.status == "sucesso"]
        duracoes = [e.duracao_ms or 0 for e in execucoes]
        estrategias: dict[str, int] = {}
        for e in sucessos:
            estrategias[e.estrategia or "-"] = estrategias.get(e.estrategia or "-", 0) + 1
        metricas.append(
            MetricaFonte(
                fonte=fonte,
                execucoes=len(execucoes),
                sucessos=len(sucessos),
                taxa_sucesso=round(len(sucessos) / len(execucoes), 4),
                duracao_media_ms=round(sum(duracoes) / len(duracoes)),
                duracao_p95_ms=percentil(duracoes, 95),
                registros_gravados=sum(e.registros_gravados for e in execucoes),
                ultima_execucao=execucoes[-1].iniciada_em,
                ultimo_status=execucoes[-1].status,
                estrategias=estrategias,
            )
        )
    return metricas


@dataclass(frozen=True)
class Premissa:
    valor: Decimal
    data_referencia: date


@dataclass(frozen=True)
class Premissas:
    """Taxas em % ao ano, no formato que a API de carteira de renda fixa aceita."""

    cdi: Premissa | None
    selic: Premissa | None
    ipca: Premissa | None


def anualizar_taxa_diaria(percentual_ao_dia: Decimal) -> Decimal:
    fator = (1 + percentual_ao_dia / 100) ** DIAS_UTEIS_ANO
    return ((fator - 1) * 100).quantize(CENTAVO_PERCENTUAL)


def acumular_mensal(percentuais_ao_mes: list[Decimal]) -> Decimal:
    fator = Decimal(1)
    for p in percentuais_ao_mes:
        fator *= 1 + p / 100
    return ((fator - 1) * 100).quantize(CENTAVO_PERCENTUAL)


async def _ultimos(sessao: AsyncSession, serie: str, quantidade: int) -> list[Indicador]:
    consulta = (
        select(Indicador)
        .where(Indicador.serie == serie)
        .order_by(Indicador.data.desc())
        .limit(quantidade)
    )
    return list((await sessao.scalars(consulta)).all())


async def calcular_premissas(sessao: AsyncSession) -> Premissas:
    cdi = await _ultimos(sessao, "cdi", 1)
    selic = await _ultimos(sessao, "selic_meta", 1)
    ipca = await _ultimos(sessao, "ipca", 12)
    return Premissas(
        cdi=Premissa(anualizar_taxa_diaria(cdi[0].valor), cdi[0].data) if cdi else None,
        selic=Premissa(selic[0].valor.quantize(CENTAVO_PERCENTUAL), selic[0].data)
        if selic
        else None,
        ipca=Premissa(acumular_mensal([p.valor for p in ipca]), ipca[0].data)
        if len(ipca) == 12
        else None,
    )
