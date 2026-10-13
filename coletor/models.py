from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from coletor.database import Base


class Indicador(Base):
    """Um ponto de uma série do SGS (Banco Central). Chave natural: série + data."""

    __tablename__ = "indicadores"

    serie: Mapped[str] = mapped_column(String(20), primary_key=True)
    data: Mapped[date] = mapped_column(Date, primary_key=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    coletado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TaxaTesouro(Base):
    """Taxa e preço de um título do Tesouro Direto numa data base."""

    __tablename__ = "taxas_tesouro"
    __table_args__ = (Index("ix_taxas_tesouro_data_base", "data_base"),)

    titulo: Mapped[str] = mapped_column(String(80), primary_key=True)
    vencimento: Mapped[date] = mapped_column(Date, primary_key=True)
    data_base: Mapped[date] = mapped_column(Date, primary_key=True)
    taxa_compra: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    taxa_venda: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pu_compra: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    pu_venda: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    coletado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Execucao(Base):
    """Registro de cada execução de coleta, base das métricas de sucesso e tempo."""

    __tablename__ = "execucoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fonte: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(15))
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalizada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duracao_ms: Mapped[int | None] = mapped_column(Integer)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    estrategia: Mapped[str | None] = mapped_column(String(20))
    registros_lidos: Mapped[int] = mapped_column(Integer, default=0)
    registros_rejeitados: Mapped[int] = mapped_column(Integer, default=0)
    registros_gravados: Mapped[int] = mapped_column(Integer, default=0)
    erro: Mapped[str | None] = mapped_column(Text)
