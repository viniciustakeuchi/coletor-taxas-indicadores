import json
import logging

import httpx
import pytest

from coletor.catalogo import FONTES_DISPONIVEIS, montar_fontes, validar_nomes
from coletor.config import Settings
from coletor.fontes.sgs import FonteSGS
from coletor.fontes.tesouro import FonteTesouro
from coletor.logs import FormatadorJson, FormatadorTexto, configurar_logs


async def test_monta_uma_fonte_por_nome() -> None:
    config = Settings(tentativas=7)
    async with httpx.AsyncClient() as cliente:
        fontes = montar_fontes(FONTES_DISPONIVEIS, cliente, config)
    assert [f.nome for f in fontes] == ["cdi", "selic", "selic_meta", "ipca", "tesouro"]
    assert isinstance(fontes[0], FonteSGS) and fontes[0].serie.codigo == 12
    assert isinstance(fontes[-1], FonteTesouro)


def test_nome_desconhecido_e_recusado() -> None:
    with pytest.raises(ValueError, match="bitcoin"):
        validar_nomes(["cdi", "bitcoin"])


def registro(**extra: object) -> logging.LogRecord:
    r = logging.makeLogRecord({"name": "coletor.teste", "levelname": "INFO", "msg": "coleta"})
    r.__dict__.update(extra)
    return r


def test_log_json_tem_uma_linha_com_os_campos_extras() -> None:
    linha = FormatadorJson().format(registro(fonte="cdi", gravados=5))
    dados = json.loads(linha)
    assert dados["evento"] == "coleta"
    assert dados["nivel"] == "info"
    assert dados["fonte"] == "cdi"
    assert dados["gravados"] == 5
    assert "\n" not in linha


def test_log_json_inclui_excecao() -> None:
    try:
        raise ValueError("quebrou")
    except ValueError:
        import sys

        r = registro()
        r.exc_info = sys.exc_info()
    assert "ValueError: quebrou" in json.loads(FormatadorJson().format(r))["excecao"]


def test_log_texto_para_desenvolvimento() -> None:
    assert FormatadorTexto().format(registro(fonte="cdi")) == "INFO    coleta fonte=cdi"


def test_configurar_logs_troca_o_formato() -> None:
    configurar_logs("json", "DEBUG")
    logger = logging.getLogger("coletor")
    assert isinstance(logger.handlers[0].formatter, FormatadorJson)
    assert logger.level == logging.DEBUG
    configurar_logs("texto")
    assert isinstance(logger.handlers[0].formatter, FormatadorTexto)
