"""Tabelas de indicadores, taxas do Tesouro e execuções.

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _coletado_em() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "coletado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "indicadores",
        sa.Column("serie", sa.String(20), primary_key=True),
        sa.Column("data", sa.Date(), primary_key=True),
        sa.Column("valor", sa.Numeric(18, 8), nullable=False),
        _coletado_em(),
    )
    op.create_table(
        "taxas_tesouro",
        sa.Column("titulo", sa.String(80), primary_key=True),
        sa.Column("vencimento", sa.Date(), primary_key=True),
        sa.Column("data_base", sa.Date(), primary_key=True),
        sa.Column("taxa_compra", sa.Numeric(10, 4)),
        sa.Column("taxa_venda", sa.Numeric(10, 4)),
        sa.Column("pu_compra", sa.Numeric(18, 2)),
        sa.Column("pu_venda", sa.Numeric(18, 2)),
        _coletado_em(),
    )
    op.create_index("ix_taxas_tesouro_data_base", "taxas_tesouro", ["data_base"])
    op.create_table(
        "execucoes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fonte", sa.String(30), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizada_em", sa.DateTime(timezone=True)),
        sa.Column("duracao_ms", sa.Integer()),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("estrategia", sa.String(20)),
        sa.Column("registros_lidos", sa.Integer(), nullable=False),
        sa.Column("registros_rejeitados", sa.Integer(), nullable=False),
        sa.Column("registros_gravados", sa.Integer(), nullable=False),
        sa.Column("erro", sa.Text()),
    )
    op.create_index("ix_execucoes_fonte", "execucoes", ["fonte"])


def downgrade() -> None:
    op.drop_index("ix_execucoes_fonte", "execucoes")
    op.drop_table("execucoes")
    op.drop_index("ix_taxas_tesouro_data_base", "taxas_tesouro")
    op.drop_table("taxas_tesouro")
    op.drop_table("indicadores")
