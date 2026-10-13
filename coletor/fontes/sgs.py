"""Séries temporais do Banco Central (SGS) pela API pública de dados abertos.

Documentação: https://dadosabertos.bcb.gov.br/dataset/11-taxa-de-juros---selic
Formato de resposta: [{"data": "02/01/2025", "valor": "0.055131"}, ...]
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import InvalidOperation
from functools import partial
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from coletor.carga import gravar_indicadores
from coletor.fontes.base import Coleta, FormatoInesperado, PontoIndicador, decimal_br
from coletor.retry import PoliticaRetry, com_retry, erro_transitorio_http

log = logging.getLogger(__name__)

# O SGS limita consultas de séries diárias a 10 anos por requisição.
JANELA_MAXIMA_DIAS = 3650


@dataclass(frozen=True)
class SerieSGS:
    chave: str
    codigo: int
    descricao: str
    janela_dias: int


SERIES: dict[str, SerieSGS] = {
    s.chave: s
    for s in (
        SerieSGS("cdi", 12, "CDI diário (% ao dia)", 10),
        SerieSGS("selic", 11, "Selic diária (% ao dia)", 10),
        SerieSGS("selic_meta", 432, "Meta Selic definida pelo Copom (% ao ano)", 10),
        # Série mensal: a janela cobre 13 meses para o IPCA acumulado em 12 meses.
        SerieSGS("ipca", 433, "IPCA mensal (% ao mês)", 400),
    )
}


def janelas(desde: date, ate: date, tamanho: int = JANELA_MAXIMA_DIAS) -> list[tuple[date, date]]:
    """Divide o período em blocos que o SGS aceita numa única consulta."""
    blocos = []
    inicio = desde
    while inicio <= ate:
        fim = min(ate, inicio + timedelta(days=tamanho - 1))
        blocos.append((inicio, fim))
        inicio = fim + timedelta(days=1)
    return blocos


def interpretar_sgs(serie: str, payload: Any) -> tuple[list[PontoIndicador], int]:
    """Converte a resposta do SGS em pontos. Itens malformados são contados e descartados."""
    if not isinstance(payload, list):
        raise FormatoInesperado(f"SGS devolveu {type(payload).__name__}, esperava lista")
    pontos: list[PontoIndicador] = []
    rejeitados = 0
    for item in payload:
        try:
            data = datetime.strptime(item["data"], "%d/%m/%Y").date()
            valor = decimal_br(str(item["valor"]))
            if not valor.is_finite():
                raise InvalidOperation
        except (KeyError, TypeError, ValueError, InvalidOperation):
            rejeitados += 1
            log.warning("item_rejeitado", extra={"fonte": serie, "item": item})
            continue
        pontos.append(PontoIndicador(serie, data, valor))
    return pontos, rejeitados


async def baixar_sgs(
    cliente: httpx.AsyncClient, url_base: str, codigo: int, desde: date, ate: date
) -> Any:
    resposta = await cliente.get(
        f"{url_base}/bcdata.sgs.{codigo}/dados",
        params={
            "formato": "json",
            "dataInicial": desde.strftime("%d/%m/%Y"),
            "dataFinal": ate.strftime("%d/%m/%Y"),
        },
    )
    # O SGS responde 404 quando não há valores no período (fim de semana, feriado).
    if resposta.status_code == 404:
        return []
    resposta.raise_for_status()
    try:
        return resposta.json()
    except ValueError as erro:
        raise FormatoInesperado("SGS não devolveu JSON") from erro


class FonteSGS:
    def __init__(
        self,
        serie: SerieSGS,
        cliente: httpx.AsyncClient,
        politica: PoliticaRetry,
        url_base: str,
    ) -> None:
        self.serie = serie
        self.nome = serie.chave
        self.janela_dias = serie.janela_dias
        self._cliente = cliente
        self._politica = politica
        self._url_base = url_base

    async def coletar(self, desde: date, ate: date) -> Coleta[PontoIndicador]:
        coleta: Coleta[PontoIndicador] = Coleta(tentativas=0, estrategia="api")
        for inicio, fim in janelas(desde, ate):
            resultado = await com_retry(
                partial(baixar_sgs, self._cliente, self._url_base, self.serie.codigo, inicio, fim),
                politica=self._politica,
                repetir_se=erro_transitorio_http,
                contexto=self.nome,
            )
            pontos, rejeitados = interpretar_sgs(self.nome, resultado.valor)
            coleta.registros.extend(pontos)
            coleta.rejeitados += rejeitados
            coleta.tentativas += resultado.tentativas
        return coleta

    async def gravar(self, sessao: AsyncSession, registros: list[PontoIndicador]) -> int:
        return await gravar_indicadores(sessao, registros)
