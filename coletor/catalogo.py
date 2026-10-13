"""Catálogo das fontes disponíveis e montagem com as configurações do ambiente."""

from functools import partial

import httpx

from coletor.config import Settings
from coletor.fontes.base import Fonte
from coletor.fontes.sgs import SERIES, FonteSGS
from coletor.fontes.tesouro import FonteTesouro, baixar_csv_tesouro, capturar_json_tesouro
from coletor.retry import PoliticaRetry

FONTES_DISPONIVEIS = [*SERIES, "tesouro"]


def politica_de(config: Settings) -> PoliticaRetry:
    return PoliticaRetry(
        tentativas=config.tentativas,
        backoff_inicial=config.backoff_inicial_segundos,
        backoff_maximo=config.backoff_maximo_segundos,
    )


def validar_nomes(nomes: list[str]) -> None:
    desconhecidas = set(nomes) - set(FONTES_DISPONIVEIS)
    if desconhecidas:
        raise ValueError(f"Fontes desconhecidas: {', '.join(sorted(desconhecidas))}")


def montar_fontes(nomes: list[str], cliente: httpx.AsyncClient, config: Settings) -> list[Fonte]:
    validar_nomes(nomes)
    politica = politica_de(config)
    fontes: list[Fonte] = []
    for nome in nomes:
        if nome in SERIES:
            fontes.append(FonteSGS(SERIES[nome], cliente, politica, config.sgs_url_base))
        else:
            fontes.append(fonte_tesouro(cliente, config))
    return fontes


def fonte_tesouro(
    cliente: httpx.AsyncClient, config: Settings, somente_csv: bool = False
) -> FonteTesouro:
    return FonteTesouro(
        capturar_json=partial(
            capturar_json_tesouro, config.tesouro_url_pagina, config.timeout_segundos
        ),
        baixar_csv=partial(baixar_csv_tesouro, cliente, config.tesouro_url_csv),
        politica=politica_de(config),
        somente_csv=somente_csv,
    )
