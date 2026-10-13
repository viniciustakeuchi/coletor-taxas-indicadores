"""Logs estruturados em JSON, uma linha por evento, prontos para CloudWatch ou Loki."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_CAMPOS_PADRAO = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class FormatadorJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dados: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "nivel": record.levelname.lower(),
            "logger": record.name,
            "evento": record.getMessage(),
        }
        dados.update({k: v for k, v in record.__dict__.items() if k not in _CAMPOS_PADRAO})
        if record.exc_info:
            dados["excecao"] = self.formatException(record.exc_info)
        return json.dumps(dados, ensure_ascii=False, default=str)


class FormatadorTexto(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in record.__dict__.items() if k not in _CAMPOS_PADRAO)
        base = f"{record.levelname:<7} {record.getMessage()}"
        return f"{base} {extras}".rstrip()


def configurar_logs(formato: str = "json", nivel: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(FormatadorJson() if formato == "json" else FormatadorTexto())
    raiz = logging.getLogger("coletor")
    raiz.handlers[:] = [handler]
    raiz.setLevel(nivel)
    raiz.propagate = False
