from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class FormatoInesperado(Exception):
    """A fonte respondeu, mas num formato que o parser não reconhece."""


@dataclass(frozen=True)
class PontoIndicador:
    serie: str
    data: date
    valor: Decimal


@dataclass(frozen=True)
class CotacaoTesouro:
    titulo: str
    vencimento: date
    data_base: date
    taxa_compra: Decimal | None
    taxa_venda: Decimal | None
    pu_compra: Decimal | None
    pu_venda: Decimal | None


@dataclass
class Coleta[R]:
    registros: list[R] = field(default_factory=list)
    rejeitados: int = 0
    tentativas: int = 1
    estrategia: str = "api"


class Fonte(Protocol):
    nome: str
    janela_dias: int

    async def coletar(self, desde: date, ate: date) -> Coleta[Any]: ...

    async def gravar(self, sessao: AsyncSession, registros: list[Any]) -> int: ...


def decimal_br(texto: str) -> Decimal:
    """Converte '1.234,56' ou '1234.56' em Decimal."""
    texto = texto.strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return Decimal(texto)
