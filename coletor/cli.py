"""Linha de comando: `coletor --help`."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
import typer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from coletor.catalogo import FONTES_DISPONIVEIS, fonte_tesouro, montar_fontes, validar_nomes
from coletor.coleta import ResumoExecucao, executar_todas
from coletor.config import settings
from coletor.database import criar_engine, criar_fabrica
from coletor.fontes.base import Fonte
from coletor.logs import configurar_logs
from coletor.metricas import calcular_premissas, metricas_por_fonte

app = typer.Typer(help="Coletor de taxas do Tesouro Direto e indicadores do Banco Central.")
log = logging.getLogger("coletor.cli")

Data = Annotated[
    datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Data no formato AAAA-MM-DD")
]


@app.callback()
def _inicio() -> None:
    configurar_logs(settings.log_formato, settings.log_nivel)


def hoje() -> date:
    return datetime.now(ZoneInfo(settings.fuso_horario)).date()


def _cliente_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.timeout_segundos,
        headers={"User-Agent": "coletor-taxas-indicadores/0.1"},
    )


async def _rodar(
    criar_fontes: Callable[[httpx.AsyncClient], list[Fonte]],
    ate: date,
    desde: date | None,
) -> list[ResumoExecucao]:
    engine = criar_engine(settings.database_url)
    try:
        async with _cliente_http() as cliente:
            return await executar_todas(criar_fontes(cliente), criar_fabrica(engine), ate, desde)
    finally:
        await engine.dispose()


def _imprimir_resumos(resumos: list[ResumoExecucao]) -> None:
    for r in resumos:
        detalhe = r.erro or (
            f"{r.gravados} gravados de {r.lidos} lidos, {r.rejeitados} rejeitados, "
            f"{r.tentativas} tentativa(s), via {r.estrategia}"
        )
        typer.echo(f"{r.fonte:<12} {r.status:<8} {r.duracao_ms:>6} ms  {detalhe}")
    if any(r.status != "sucesso" for r in resumos):
        raise typer.Exit(code=1)


@app.command()
def coletar(
    fontes: Annotated[
        list[str] | None,
        typer.Argument(help=f"Fontes a coletar (padrão: todas). Opções: {FONTES_DISPONIVEIS}"),
    ] = None,
    desde: Data = None,
    ate: Data = None,
) -> None:
    """Coleta as fontes em paralelo e grava no banco (sem datas, usa a janela de cada fonte)."""
    nomes = fontes or FONTES_DISPONIVEIS
    try:
        validar_nomes(nomes)
    except ValueError as erro:
        raise typer.BadParameter(str(erro)) from erro
    resumos = asyncio.run(
        _rodar(
            lambda cliente: montar_fontes(nomes, cliente, settings),
            ate.date() if ate else hoje(),
            desde.date() if desde else None,
        )
    )
    _imprimir_resumos(resumos)


@app.command("historico-tesouro")
def historico_tesouro(
    desde: Annotated[datetime, typer.Option(formats=["%Y-%m-%d"])],
    ate: Data = None,
) -> None:
    """Carga histórica do Tesouro Direto a partir do CSV de dados abertos."""
    resumos = asyncio.run(
        _rodar(
            lambda cliente: [fonte_tesouro(cliente, settings, somente_csv=True)],
            ate.date() if ate else hoje(),
            desde.date(),
        )
    )
    _imprimir_resumos(resumos)


def criar_agendador(tarefa: Callable[[], Awaitable[Any]]) -> AsyncIOScheduler:
    agendador = AsyncIOScheduler(timezone=settings.fuso_horario)
    agendador.add_job(
        tarefa,
        CronTrigger.from_crontab(settings.agenda_cron, timezone=settings.fuso_horario),
        id="coleta_diaria",
        max_instances=1,  # nunca duas coletas ao mesmo tempo
        coalesce=True,  # se o processo ficou parado, roda uma vez só ao voltar
        misfire_grace_time=3600,
    )
    return agendador


@app.command()
def agendar(
    agora: Annotated[bool, typer.Option(help="Roda uma coleta logo ao iniciar")] = False,
) -> None:
    """Mantém o processo vivo e coleta todas as fontes no horário de `AGENDA_CRON`."""

    async def coleta_agendada() -> None:
        await _rodar(
            lambda cliente: montar_fontes(FONTES_DISPONIVEIS, cliente, settings), hoje(), None
        )

    async def principal() -> None:  # pragma: no cover - laço infinito
        agendador = criar_agendador(coleta_agendada)
        agendador.start()
        log.info("agendador_iniciado", extra={"cron": settings.agenda_cron})
        if agora:
            await coleta_agendada()
        await asyncio.Event().wait()

    asyncio.run(principal())  # pragma: no cover


@app.command()
def relatorio(
    dias: Annotated[int, typer.Option(help="Janela em dias")] = 30,
    formato: Annotated[str, typer.Option(help="tabela ou json")] = "tabela",
) -> None:
    """Taxa de sucesso, tempo por execução e volume gravado por fonte."""

    async def consultar() -> list[Any]:
        engine = criar_engine(settings.database_url)
        try:
            async with criar_fabrica(engine)() as sessao:
                return list(await metricas_por_fonte(sessao, dias))
        finally:
            await engine.dispose()

    metricas = asyncio.run(consultar())
    if formato == "json":
        typer.echo(json.dumps([asdict(m) for m in metricas], default=str, indent=2))
        return
    typer.echo(
        f"{'fonte':<18}{'execuções':>10}{'sucesso':>9}{'média':>10}{'p95':>10}{'gravados':>10}"
    )
    for m in metricas:
        typer.echo(
            f"{m.fonte:<18}{m.execucoes:>10}{m.taxa_sucesso:>9.0%}"
            f"{m.duracao_media_ms:>8}ms{m.duracao_p95_ms:>8}ms{m.registros_gravados:>10}"
        )


@app.command()
def premissas() -> None:
    """CDI, Selic e IPCA 12 meses em % ao ano, a partir das últimas séries coletadas."""

    async def consultar() -> dict[str, Any]:
        engine = criar_engine(settings.database_url)
        try:
            async with criar_fabrica(engine)() as sessao:
                return asdict(await calcular_premissas(sessao))
        finally:
            await engine.dispose()

    typer.echo(json.dumps(asyncio.run(consultar()), default=str, indent=2, ensure_ascii=False))
