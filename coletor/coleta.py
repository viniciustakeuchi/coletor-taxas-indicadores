"""Orquestra as coletas: cada fonte roda isolada, em paralelo, e registra a execução."""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coletor.fontes.base import Fonte
from coletor.models import Execucao

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResumoExecucao:
    fonte: str
    status: str
    duracao_ms: int
    tentativas: int
    estrategia: str | None
    lidos: int
    rejeitados: int
    gravados: int
    erro: str | None = None


async def executar(
    fonte: Fonte,
    fabrica: async_sessionmaker[AsyncSession],
    ate: date,
    desde: date | None = None,
) -> ResumoExecucao:
    desde = desde or ate - timedelta(days=fonte.janela_dias)
    inicio = time.perf_counter()
    async with fabrica() as sessao:
        execucao = Execucao(fonte=fonte.nome, status="em_andamento", iniciada_em=datetime.now(UTC))
        sessao.add(execucao)
        await sessao.commit()

        log.info("coleta_iniciada", extra={"fonte": fonte.nome, "desde": desde, "ate": ate})
        try:
            coleta = await fonte.coletar(desde, ate)
            gravados = await fonte.gravar(sessao, coleta.registros)
            execucao.status = "sucesso"
            execucao.tentativas = coleta.tentativas
            execucao.estrategia = coleta.estrategia
            execucao.registros_lidos = len(coleta.registros) + coleta.rejeitados
            execucao.registros_rejeitados = coleta.rejeitados
            execucao.registros_gravados = gravados
        except Exception as erro:
            await sessao.rollback()
            await sessao.refresh(execucao)
            execucao.status = "falha"
            execucao.erro = repr(erro)[:2000]
            log.exception("coleta_falhou", extra={"fonte": fonte.nome})

        execucao.finalizada_em = datetime.now(UTC)
        execucao.duracao_ms = round((time.perf_counter() - inicio) * 1000)
        sessao.add(execucao)
        await sessao.commit()

    resumo = ResumoExecucao(
        fonte=fonte.nome,
        status=execucao.status,
        duracao_ms=execucao.duracao_ms,
        tentativas=execucao.tentativas,
        estrategia=execucao.estrategia,
        lidos=execucao.registros_lidos,
        rejeitados=execucao.registros_rejeitados,
        gravados=execucao.registros_gravados,
        erro=execucao.erro,
    )
    log.info("coleta_finalizada", extra={k: v for k, v in vars(resumo).items() if v is not None})
    return resumo


async def executar_todas(
    fontes: Sequence[Fonte],
    fabrica: async_sessionmaker[AsyncSession],
    ate: date,
    desde: date | None = None,
) -> list[ResumoExecucao]:
    return list(await asyncio.gather(*(executar(f, fabrica, ate, desde) for f in fontes)))
