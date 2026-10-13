from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://coletor:coletor@localhost:5432/coletor"

    # Rede e resiliência
    timeout_segundos: float = 30.0
    tentativas: int = 4
    backoff_inicial_segundos: float = 2.0
    backoff_maximo_segundos: float = 30.0

    # Fontes
    sgs_url_base: str = "https://api.bcb.gov.br/dados/serie"
    tesouro_url_api: str = "https://www.tesourodireto.com.br/o/rentabilidade/resgatar"
    tesouro_url_pagina: str = (
        "https://www.tesourodireto.com.br/produtos/dados-sobre-titulos/rendimento-dos-titulos"
    )
    tesouro_url_csv: str = (
        "https://www.tesourotransparente.gov.br/ckan/dataset/"
        "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
        "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv"
    )

    # Agendamento: de segunda a sexta às 19h (depois do fechamento do mercado).
    # Use nomes de dia: no APScheduler 3, "1-5" significa terça a sábado.
    agenda_cron: str = "0 19 * * mon-fri"
    fuso_horario: str = "America/Sao_Paulo"

    log_formato: Literal["json", "texto"] = "json"
    log_nivel: str = "INFO"


settings = Settings()
