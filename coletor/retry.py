"""Retry assíncrono com backoff exponencial e jitter completo.

A espera antes da tentativa n é um valor aleatório entre 0 e
min(maximo, inicial * 2^(n-1)), como recomendado no artigo
"Exponential Backoff And Jitter" da AWS. O jitter evita que várias
coletas que falharam juntas voltem a bater na fonte ao mesmo tempo.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoliticaRetry:
    tentativas: int = 4
    backoff_inicial: float = 2.0
    backoff_maximo: float = 30.0

    def espera(self, tentativa: int, aleatorio: Callable[[], float] = random.random) -> float:
        teto = min(self.backoff_maximo, self.backoff_inicial * 2.0 ** (tentativa - 1))
        return aleatorio() * teto


@dataclass
class Resultado[T]:
    valor: T
    tentativas: int


def erro_transitorio_http(erro: BaseException) -> bool:
    """Falhas de rede, 429 e 5xx valem uma nova tentativa; 4xx não."""
    if isinstance(erro, httpx.TransportError):
        return True
    if isinstance(erro, httpx.HTTPStatusError):
        status = erro.response.status_code
        return status == 429 or status >= 500
    return False


async def com_retry[T](
    operacao: Callable[[], Awaitable[T]],
    *,
    politica: PoliticaRetry,
    repetir_se: Callable[[BaseException], bool],
    contexto: str = "",
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Resultado[T]:
    for tentativa in range(1, politica.tentativas + 1):
        try:
            return Resultado(await operacao(), tentativa)
        except Exception as erro:
            if tentativa == politica.tentativas or not repetir_se(erro):
                raise
            espera = politica.espera(tentativa)
            log.warning(
                "tentativa_falhou",
                extra={
                    "contexto": contexto,
                    "tentativa": tentativa,
                    "erro": repr(erro),
                    "espera_s": round(espera, 2),
                },
            )
            await dormir(espera)
    raise AssertionError("inalcançável")  # pragma: no cover
