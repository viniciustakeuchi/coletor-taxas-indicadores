from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coletor.carga import gravar_indicadores
from coletor.coleta import executar, executar_todas
from coletor.fontes.base import Coleta, PontoIndicador
from coletor.models import Execucao, Indicador


class FonteFalsa:
    janela_dias = 10

    def __init__(self, nome: str, pontos: int = 3, erro: Exception | None = None) -> None:
        self.nome = nome
        self.pontos = pontos
        self.erro = erro
        self.periodos: list[tuple[date, date]] = []

    async def coletar(self, desde: date, ate: date) -> Coleta[PontoIndicador]:
        self.periodos.append((desde, ate))
        if self.erro:
            raise self.erro
        registros = [
            PontoIndicador(self.nome, date(2025, 10, d), Decimal("0.05"))
            for d in range(1, self.pontos + 1)
        ]
        return Coleta(registros, rejeitados=1, tentativas=2, estrategia="api")

    async def gravar(self, sessao: AsyncSession, registros: list[Any]) -> int:
        return await gravar_indicadores(sessao, registros)


async def test_execucao_bem_sucedida_fica_registrada(
    fabrica: async_sessionmaker[AsyncSession], sessao: AsyncSession
) -> None:
    resumo = await executar(FonteFalsa("cdi"), fabrica, ate=date(2025, 10, 7))

    assert resumo.status == "sucesso"
    assert (resumo.lidos, resumo.rejeitados, resumo.gravados) == (4, 1, 3)
    execucao = await sessao.scalar(select(Execucao))
    assert execucao is not None
    assert execucao.status == "sucesso"
    assert execucao.tentativas == 2
    assert execucao.estrategia == "api"
    assert execucao.duracao_ms is not None and execucao.duracao_ms >= 0
    assert execucao.finalizada_em is not None


async def test_janela_padrao_vem_da_fonte(fabrica: async_sessionmaker[AsyncSession]) -> None:
    fonte = FonteFalsa("cdi")
    await executar(fonte, fabrica, ate=date(2025, 10, 11))
    assert fonte.periodos == [(date(2025, 10, 1), date(2025, 10, 11))]

    await executar(fonte, fabrica, ate=date(2025, 10, 11), desde=date(2020, 1, 1))
    assert fonte.periodos[-1] == (date(2020, 1, 1), date(2025, 10, 11))


async def test_falha_fica_registrada_com_o_erro(
    fabrica: async_sessionmaker[AsyncSession], sessao: AsyncSession
) -> None:
    resumo = await executar(
        FonteFalsa("selic", erro=RuntimeError("fonte fora do ar")), fabrica, ate=date(2025, 10, 7)
    )
    assert resumo.status == "falha"
    assert resumo.erro is not None and "fonte fora do ar" in resumo.erro
    execucao = await sessao.scalar(select(Execucao))
    assert execucao is not None and execucao.status == "falha"


async def test_falha_numa_fonte_nao_derruba_as_outras(
    fabrica: async_sessionmaker[AsyncSession], sessao: AsyncSession
) -> None:
    resumos = await executar_todas(
        [FonteFalsa("cdi"), FonteFalsa("ipca", erro=TimeoutError()), FonteFalsa("selic")],
        fabrica,
        ate=date(2025, 10, 7),
    )
    assert [r.status for r in resumos] == ["sucesso", "falha", "sucesso"]
    assert await sessao.scalar(select(func.count()).select_from(Indicador)) == 6
    assert await sessao.scalar(select(func.count()).select_from(Execucao)) == 3


async def test_segunda_execucao_nao_grava_nada_novo(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    primeira = await executar(FonteFalsa("cdi"), fabrica, ate=date(2025, 10, 7))
    segunda = await executar(FonteFalsa("cdi"), fabrica, ate=date(2025, 10, 7))
    assert (primeira.gravados, segunda.gravados) == (3, 0)
