"""Taxas e preços do Tesouro Direto, em três camadas.

1. API: o site do Tesouro Direto monta a tabela de rentabilidade a partir de um
   JSON público (`/o/rentabilidade/resgatar`), lido direto com httpx.
2. Navegador: se a API mudar de endereço ou passar a recusar clientes HTTP, um
   Chromium headless (Playwright) abre a página e captura o JSON que ela mesma
   carrega, qualquer que seja a URL.
3. CSV: se o site inteiro falhar, a fonte baixa o CSV de dados abertos do Tesouro
   Transparente, que traz o histórico diário de todos os títulos com um dia de atraso.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from playwright.async_api import Error as ErroPlaywright
from playwright.async_api import Response, Route, async_playwright
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from coletor.carga import gravar_taxas_tesouro
from coletor.fontes.base import Coleta, CotacaoTesouro, FormatoInesperado, decimal_br
from coletor.retry import PoliticaRetry, com_retry, erro_transitorio_http

log = logging.getLogger(__name__)

TRECHO_URL_API = "/o/rentabilidade/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0 Safari/537.36"
)
RECURSOS_IGNORADOS = {"image", "font", "media", "stylesheet"}
PERCENTUAL = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*%")


def normalizar_titulo(nome: str) -> str:
    """'Tesouro IPCA+ 2035' -> 'Tesouro IPCA+'. O vencimento fica em coluna própria.

    No Renda+ e no Educa+ o ano do nome é o da aposentadoria ou da faculdade, não o
    vencimento, então o ano nunca serve como chave.
    """
    return re.sub(r"\s+\d{4}$", "", " ".join(nome.split()))


def _positivo(valor: Decimal | None) -> Decimal | None:
    """O site informa 0 quando o título não está disponível para compra."""
    return valor if valor is not None and valor > 0 else None


def taxa_do_texto(texto: str | None) -> Decimal | None:
    """'IPCA + 6,82%' -> 6.82; 'SELIC + 0,0983%' -> 0.0983; 'prefixado' -> None."""
    encontrado = PERCENTUAL.search(texto or "")
    return decimal_br(encontrado.group(1)) if encontrado else None


# ---------------------------------------------------------------- JSON do site


class _TituloSite(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nome: str = Field(alias="treasuryBondName")
    vencimento: datetime = Field(alias="maturityDate")
    data_base: datetime = Field(alias="lastMarketPricingDate")
    texto_taxa_compra: str | None = Field(None, alias="investmentProfitabilityIndexerName")
    texto_taxa_venda: str | None = Field(None, alias="redemptionProfitabilityFeeIndexerName")
    pu_compra: Decimal | None = Field(None, alias="unitaryInvestmentValue")
    pu_venda: Decimal | None = Field(None, alias="unitaryRedemptionValue")

    @field_validator("vencimento", "data_base", mode="before")
    @classmethod
    def _iso(cls, valor: Any) -> Any:
        # O site manda '2031-03-01T00:00', sem segundos.
        return datetime.fromisoformat(valor) if isinstance(valor, str) else valor


def interpretar_json_tesouro(payload: Any) -> tuple[list[CotacaoTesouro], int]:
    """Lê o JSON de rentabilidade: um dicionário de segmentos, cada um com uma lista de títulos."""
    if not isinstance(payload, dict) or not payload:
        raise FormatoInesperado("JSON do Tesouro vazio ou fora do formato esperado")
    listas = [v for v in payload.values() if isinstance(v, list)]
    if not listas or not any(
        isinstance(i, dict) and "treasuryBondName" in i for lista in listas for i in lista
    ):
        raise FormatoInesperado(f"JSON do Tesouro sem títulos: chaves {list(payload)[:5]}")

    cotacoes: list[CotacaoTesouro] = []
    rejeitados = 0
    for item in (i for lista in listas for i in lista):
        try:
            titulo = _TituloSite.model_validate(item)
            taxa_compra = taxa_do_texto(titulo.texto_taxa_compra)
            taxa_venda = taxa_do_texto(titulo.texto_taxa_venda)
        except (TypeError, ValueError, InvalidOperation, ValidationError):
            rejeitados += 1
            log.warning("item_rejeitado", extra={"fonte": "tesouro", "item": str(item)[:200]})
            continue
        cotacoes.append(
            CotacaoTesouro(
                titulo=normalizar_titulo(titulo.nome),
                vencimento=titulo.vencimento.date(),
                data_base=titulo.data_base.date(),
                taxa_compra=taxa_compra,
                taxa_venda=taxa_venda,
                pu_compra=_positivo(titulo.pu_compra),
                pu_venda=_positivo(titulo.pu_venda),
            )
        )
    return cotacoes, rejeitados


async def baixar_json_tesouro(cliente: httpx.AsyncClient, url: str) -> Any:
    resposta = await cliente.get(url, headers={"User-Agent": USER_AGENT})
    resposta.raise_for_status()
    try:
        return resposta.json()
    except ValueError as erro:
        raise FormatoInesperado("API do Tesouro não devolveu JSON") from erro


async def capturar_json_tesouro(url_pagina: str, timeout_s: float) -> Any:
    """Abre a página no Chromium e devolve o JSON de rentabilidade que ela carrega."""
    timeout_ms = timeout_s * 1000

    async def bloquear_pesados(rota: Route) -> None:
        if rota.request.resource_type in RECURSOS_IGNORADOS:
            await rota.abort()
        else:
            await rota.continue_()

    def eh_json_titulos(resposta: Response) -> bool:
        return TRECHO_URL_API in resposta.url and resposta.request.resource_type in {
            "xhr",
            "fetch",
        }

    async with async_playwright() as pw:
        navegador = await pw.chromium.launch(headless=True)
        try:
            contexto = await navegador.new_context(locale="pt-BR", user_agent=USER_AGENT)
            await contexto.route("**/*", bloquear_pesados)
            pagina = await contexto.new_page()
            async with pagina.expect_response(eh_json_titulos, timeout=timeout_ms) as captura:
                await pagina.goto(url_pagina, wait_until="domcontentloaded", timeout=timeout_ms)
            resposta = await captura.value
            if not resposta.ok:
                raise ErroPlaywright(f"JSON do Tesouro respondeu HTTP {resposta.status}")
            return await resposta.json()
        finally:
            await navegador.close()


def erro_transitorio_navegador(erro: BaseException) -> bool:
    return isinstance(erro, ErroPlaywright)


# -------------------------------------------------------- CSV do Tesouro Transparente

COLUNAS_CSV = {
    "titulo": "Tipo Titulo",
    "vencimento": "Data Vencimento",
    "data_base": "Data Base",
    "taxa_compra": "Taxa Compra Manha",
    "taxa_venda": "Taxa Venda Manha",
    "pu_compra": "PU Compra Manha",
    "pu_venda": "PU Venda Manha",
}


def _data_br(texto: str) -> date:
    return datetime.strptime(texto.strip(), "%d/%m/%Y").date()


def _decimal_opcional(texto: str) -> Decimal | None:
    return _positivo(decimal_br(texto)) if texto.strip() else None


def interpretar_csv_tesouro(
    linhas: Iterable[str], desde: date | None = None, ate: date | None = None
) -> tuple[list[CotacaoTesouro], int]:
    iterador = iter(linhas)
    cabecalho = [c.strip() for c in next(iterador, "").lstrip("﻿").split(";")]
    try:
        posicao = {campo: cabecalho.index(coluna) for campo, coluna in COLUNAS_CSV.items()}
    except ValueError as erro:
        raise FormatoInesperado(f"Cabeçalho do CSV mudou: {cabecalho}") from erro

    cotacoes: list[CotacaoTesouro] = []
    rejeitados = 0
    for linha in iterador:
        if not linha.strip():
            continue
        campos = linha.split(";")
        try:
            data_base = _data_br(campos[posicao["data_base"]])
            if (desde and data_base < desde) or (ate and data_base > ate):
                continue
            cotacoes.append(
                CotacaoTesouro(
                    titulo=" ".join(campos[posicao["titulo"]].split()),
                    vencimento=_data_br(campos[posicao["vencimento"]]),
                    data_base=data_base,
                    taxa_compra=_decimal_opcional(campos[posicao["taxa_compra"]]),
                    taxa_venda=_decimal_opcional(campos[posicao["taxa_venda"]]),
                    pu_compra=_decimal_opcional(campos[posicao["pu_compra"]]),
                    pu_venda=_decimal_opcional(campos[posicao["pu_venda"]]),
                )
            )
        except (IndexError, ValueError, InvalidOperation):
            rejeitados += 1
            log.warning("linha_rejeitada", extra={"fonte": "tesouro", "linha": linha[:200]})
    return cotacoes, rejeitados


def decodificar_csv(conteudo: bytes) -> str:
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("latin-1")


async def baixar_csv_tesouro(cliente: httpx.AsyncClient, url: str) -> str:
    resposta = await cliente.get(url, follow_redirects=True)
    resposta.raise_for_status()
    return decodificar_csv(resposta.content)


# ---------------------------------------------------------------------- Fonte


class FonteTesouro:
    nome = "tesouro"
    janela_dias = 5

    def __init__(
        self,
        baixar_json: Callable[[], Awaitable[Any]],
        capturar_json: Callable[[], Awaitable[Any]],
        baixar_csv: Callable[[], Awaitable[str]],
        politica: PoliticaRetry,
        somente_csv: bool = False,
    ) -> None:
        self._estrategias_json: list[tuple[str, Callable[[], Awaitable[Any]], Any]] = [
            ("api", baixar_json, erro_transitorio_http),
            ("navegador", capturar_json, erro_transitorio_navegador),
        ]
        self._baixar_csv = baixar_csv
        self._politica = politica
        self._somente_csv = somente_csv
        if somente_csv:
            self.nome = "tesouro_historico"

    async def coletar(self, desde: date, ate: date) -> Coleta[CotacaoTesouro]:
        tentativas = 0

        def contando(operacao: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[Any]]:
            async def chamar() -> Any:
                nonlocal tentativas
                tentativas += 1
                return await operacao()

            return chamar

        if not self._somente_csv:
            for estrategia, operacao, repetir_se in self._estrategias_json:
                try:
                    resultado = await com_retry(
                        contando(operacao),
                        politica=self._politica,
                        repetir_se=repetir_se,
                        contexto=f"tesouro:{estrategia}",
                    )
                    cotacoes, rejeitados = interpretar_json_tesouro(resultado.valor)
                    return Coleta(cotacoes, rejeitados, tentativas, estrategia)
                except Exception as erro:
                    log.warning(
                        "estrategia_falhou",
                        extra={"fonte": self.nome, "estrategia": estrategia, "erro": repr(erro)},
                    )

        resultado_csv = await com_retry(
            contando(self._baixar_csv),
            politica=self._politica,
            repetir_se=erro_transitorio_http,
            contexto="tesouro:csv",
        )
        cotacoes, rejeitados = interpretar_csv_tesouro(resultado_csv.valor.splitlines(), desde, ate)
        return Coleta(cotacoes, rejeitados, tentativas, "csv")

    async def gravar(self, sessao: AsyncSession, registros: list[CotacaoTesouro]) -> int:
        return await gravar_taxas_tesouro(sessao, registros)
