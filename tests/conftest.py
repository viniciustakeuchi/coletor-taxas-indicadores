import json
import os
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from coletor.database import Base, criar_fabrica

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_texto(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


def fixture_json(nome: str) -> Any:
    return json.loads(fixture_texto(nome))


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """SQLite em arquivo temporário por padrão; com TEST_DATABASE_URL, roda no Postgres.

    O arquivo (e não `:memory:`) dá a cada sessão sua própria conexão, como no Postgres,
    o que importa para os testes de coletas em paralelo.
    """
    url = os.getenv("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'teste.db'}"
    motor = create_async_engine(url)
    async with motor.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield motor
    async with motor.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await motor.dispose()


@pytest.fixture
def fabrica(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return criar_fabrica(engine)


@pytest.fixture
async def sessao(fabrica: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with fabrica() as s:
        yield s


# ------------------------------------------------------- site falso do Tesouro


@dataclass
class SiteFalso:
    url: str
    status_json: int = 200
    servir_json: bool = True
    requisicoes: list[str] = field(default_factory=list)

    @property
    def url_pagina(self) -> str:
        return f"{self.url}/produtos/dados-sobre-titulos/rendimento-dos-titulos"


@pytest.fixture
def site_tesouro() -> Iterator[SiteFalso]:
    """Servidor HTTP local que imita a página de rendimento dos títulos, sem internet."""
    pagina = fixture_texto("pagina_tesouro.html").encode()
    dados = fixture_texto("tesouro_rentabilidade.json").encode()
    site = SiteFalso(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            site.requisicoes.append(self.path)
            if self.path.startswith("/produtos/dados-sobre-titulos/rendimento-dos-titulos"):
                self._responder(200, "text/html; charset=utf-8", pagina)
            elif self.path.startswith("/o/rentabilidade/") and site.servir_json:
                self._responder(site.status_json, "application/json", dados)
            else:
                self._responder(404, "text/plain", b"nao encontrado")

        def _responder(self, status: int, tipo: str, corpo: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *args: Any) -> None:
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    site.url = f"http://127.0.0.1:{servidor.server_address[1]}"
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    yield site
    servidor.shutdown()
    servidor.server_close()
