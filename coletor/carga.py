"""Carga idempotente: rodar a mesma coleta duas vezes não duplica nem reescreve linhas.

Cada tabela tem uma chave natural (série + data, título + vencimento + data base).
A gravação é um upsert que só atualiza a linha quando algum valor mudou, então
o número devolvido é exatamente quantas linhas foram inseridas ou corrigidas.
"""

from collections.abc import Sequence
from dataclasses import asdict
from itertools import batched
from typing import Any, cast

from sqlalchemy import CursorResult, Table, func, or_
from sqlalchemy.dialects.postgresql import insert as insert_postgres
from sqlalchemy.dialects.sqlite import insert as insert_sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from coletor.fontes.base import CotacaoTesouro, PontoIndicador
from coletor.models import Indicador, TaxaTesouro

TAMANHO_LOTE = 1000


async def upsert(
    sessao: AsyncSession,
    tabela: Table,
    linhas: Sequence[dict[str, Any]],
    chaves: Sequence[str],
) -> int:
    if not linhas:
        return 0
    # Uma mesma chave duas vezes no lote faria o Postgres recusar o ON CONFLICT.
    unicas = list({tuple(linha[c] for c in chaves): linha for linha in linhas}.values())
    valores = [c for c in unicas[0] if c not in chaves]

    dialeto = sessao.get_bind().dialect.name
    if dialeto == "postgresql":
        insert = insert_postgres
    elif dialeto == "sqlite":
        insert = insert_sqlite  # type: ignore[assignment]
    else:  # pragma: no cover
        raise NotImplementedError(f"Banco não suportado: {dialeto}")

    gravadas = 0
    for lote in batched(unicas, TAMANHO_LOTE):
        stmt = insert(tabela).values(list(lote))
        novo = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=list(chaves),
            set_={**{c: novo[c] for c in valores}, "coletado_em": func.now()},
            where=or_(*(tabela.c[c].is_distinct_from(novo[c]) for c in valores)),
        )
        resultado = cast(CursorResult[Any], await sessao.execute(stmt))
        gravadas += resultado.rowcount
    return gravadas


async def gravar_indicadores(sessao: AsyncSession, pontos: Sequence[PontoIndicador]) -> int:
    tabela = cast(Table, Indicador.__table__)
    return await upsert(sessao, tabela, [asdict(p) for p in pontos], ["serie", "data"])


async def gravar_taxas_tesouro(sessao: AsyncSession, cotacoes: Sequence[CotacaoTesouro]) -> int:
    tabela = cast(Table, TaxaTesouro.__table__)
    return await upsert(
        sessao, tabela, [asdict(c) for c in cotacoes], ["titulo", "vencimento", "data_base"]
    )
