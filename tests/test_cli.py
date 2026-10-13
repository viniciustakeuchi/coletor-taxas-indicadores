import asyncio
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from coletor import cli
from coletor.carga import gravar_indicadores
from coletor.config import settings
from coletor.database import Base, criar_fabrica
from coletor.fontes.base import PontoIndicador
from tests.test_coleta import FonteFalsa

runner = CliRunner()


@pytest.fixture
def banco(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"

    async def criar() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(criar())
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "log_formato", "texto")
    yield url


def usar_fontes(monkeypatch: pytest.MonkeyPatch, *fontes: FonteFalsa) -> None:
    monkeypatch.setattr(cli, "montar_fontes", lambda nomes, cliente, config: list(fontes))


def test_coletar_imprime_resumo_e_sai_com_zero(banco: str, monkeypatch: pytest.MonkeyPatch) -> None:
    usar_fontes(monkeypatch, FonteFalsa("cdi"), FonteFalsa("selic"))
    resultado = runner.invoke(cli.app, ["coletar", "cdi", "selic", "--ate", "2025-10-07"])
    assert resultado.exit_code == 0, resultado.output
    assert "cdi" in resultado.stdout and "sucesso" in resultado.stdout
    assert "3 gravados de 4 lidos" in resultado.stdout


def test_coletar_sai_com_erro_se_alguma_fonte_falhar(
    banco: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    usar_fontes(monkeypatch, FonteFalsa("cdi"), FonteFalsa("ipca", erro=RuntimeError("fora")))
    resultado = runner.invoke(cli.app, ["coletar", "--desde", "2025-10-01"])
    assert resultado.exit_code == 1
    assert "falha" in resultado.stdout


def test_coletar_recusa_fonte_desconhecida(banco: str) -> None:
    resultado = runner.invoke(cli.app, ["coletar", "bitcoin"])
    assert resultado.exit_code == 2


def test_historico_do_tesouro_usa_so_o_csv(banco: str, monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []

    def fonte_tesouro(cliente: Any, config: Any, somente_csv: bool = False) -> FonteFalsa:
        chamadas.append({"somente_csv": somente_csv})
        return FonteFalsa("tesouro_historico")

    monkeypatch.setattr(cli, "fonte_tesouro", fonte_tesouro)
    resultado = runner.invoke(
        cli.app, ["historico-tesouro", "--desde", "2024-01-01", "--ate", "2024-12-31"]
    )
    assert resultado.exit_code == 0, resultado.output
    assert chamadas == [{"somente_csv": True}]


def test_relatorio_em_tabela_e_json(banco: str, monkeypatch: pytest.MonkeyPatch) -> None:
    usar_fontes(monkeypatch, FonteFalsa("cdi"))
    runner.invoke(cli.app, ["coletar", "cdi"])
    runner.invoke(cli.app, ["coletar", "cdi"])

    tabela = runner.invoke(cli.app, ["relatorio"])
    assert tabela.exit_code == 0
    assert "cdi" in tabela.stdout and "100%" in tabela.stdout

    saida = runner.invoke(cli.app, ["relatorio", "--formato", "json"])
    [metrica] = json.loads(saida.stdout)
    assert metrica["execucoes"] == 2
    assert metrica["registros_gravados"] == 3


def test_premissas_em_json(banco: str) -> None:
    async def popular() -> None:
        engine = create_async_engine(banco)
        async with criar_fabrica(engine)() as sessao:
            await gravar_indicadores(
                sessao, [PontoIndicador("cdi", date(2025, 10, 7), Decimal("0.055131"))]
            )
            await sessao.commit()
        await engine.dispose()

    asyncio.run(popular())
    resultado = runner.invoke(cli.app, ["premissas"])
    dados = json.loads(resultado.stdout)
    assert dados["cdi"] == {"valor": "14.90", "data_referencia": "2025-10-07"}
    assert dados["ipca"] is None


def test_agendador_roda_em_dias_uteis_as_19h() -> None:
    async def tarefa() -> None:
        pass

    agendador = cli.criar_agendador(tarefa)
    [job] = agendador.get_jobs()
    assert job.id == "coleta_diaria"
    assert job.max_instances == 1

    fuso = ZoneInfo("America/Sao_Paulo")
    momento = datetime(2025, 10, 5, tzinfo=fuso)  # um domingo
    disparos = []
    for _ in range(6):
        momento = job.trigger.get_next_fire_time(None, momento)
        disparos.append(momento.strftime("%a %H:%M"))
        momento += timedelta(minutes=1)
    assert disparos == [
        "Mon 19:00",
        "Tue 19:00",
        "Wed 19:00",
        "Thu 19:00",
        "Fri 19:00",
        "Mon 19:00",
    ]
