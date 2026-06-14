"""
src/etl/loader.py

Responsabilidade única: persistir o TransformResult no banco de dados.
Sem transformação, sem lógica de negócio.

Duas funções públicas:
  fetch_pipeline_context(db) → lê o contexto pré-transformação (item IDs, retainers, etc.)
  load(db, result)           → escreve todos os registros no banco

Estratégia por tabela:
  market_snapshots    → INSERT ON CONFLICT (item_id, collected_at) DO NOTHING
  sale_history        → INSERT ON CONFLICT (...chave natural...) DO NOTHING
  my_active_listings  → INSERT ON CONFLICT (retainer_id, item_id, is_hq) DO UPDATE
  my_inferred_sales   → INSERT simples (sem conflito esperado)
  my_active_listings  → DELETE das listagens inferidas como vendidas
"""

import logging
import time
from collections.abc import Iterator

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from src.db.models import MyActiveListing
from src.etl.transformer import TransformResult

log = logging.getLogger(__name__)

CHUNK = 500   # linhas por batch de INSERT (seguro para PostgreSQL)


# ---------------------------------------------------------------------------
# Utilitário interno
# ---------------------------------------------------------------------------

def _chunks(lst: list, size: int) -> Iterator[list]:
    for i in range(0, len(lst), size):
        yield lst[i: i + size]


# ---------------------------------------------------------------------------
# Leitura de contexto pré-transformação
# ---------------------------------------------------------------------------

def fetch_pipeline_context(db: Session) -> dict:
    """
    Lê do banco todos os dados que o transformer precisa antes de rodar.
    Chamado pelo scheduler antes de extract() + transform().

    Retorna dict com:
      known_item_ids  → frozenset[int]  — IDs presentes na tabela items
      retainer_map    → dict[str, int]  — {retainer_name: retainer_id}
      previous_active → list[dict]      — linhas atuais de my_active_listings
    """
    # Todos os item_ids do catálogo
    rows = db.execute(text("SELECT item_id FROM items")).fetchall()
    known_item_ids = frozenset(r[0] for r in rows)

    # Meus retainers: name → id
    rows = db.execute(text("SELECT id, name FROM my_retainers")).fetchall()
    retainer_map: dict[str, int] = {r.name: r.id for r in rows}

    # Listagens ativas atuais dos meus retainers (com o nome para matching)
    rows = db.execute(text("""
        SELECT
            mal.id,
            mal.retainer_id,
            mr.name          AS retainer_name,
            mal.item_id,
            mal.is_hq,
            mal.price_per_unit,
            mal.quantity,
            mal.first_seen_at,
            mal.last_seen_at
        FROM my_active_listings mal
        JOIN my_retainers mr ON mr.id = mal.retainer_id
    """)).fetchall()
    previous_active = [dict(r._mapping) for r in rows]

    log.info(
        f"Contexto: {len(known_item_ids):,} itens no catálogo | "
        f"{len(retainer_map)} retainer(s) | "
        f"{len(previous_active)} listagem(ns) ativa(s)"
    )
    return {
        "known_item_ids":  known_item_ids,
        "retainer_map":    retainer_map,
        "previous_active": previous_active,
    }


# ---------------------------------------------------------------------------
# Ponto de entrada da escrita
# ---------------------------------------------------------------------------

def load(db: Session, result: TransformResult) -> None:
    """
    Persiste todos os dados do TransformResult no banco.
    Chamado pelo scheduler após transform() retornar.

    Cada operação faz seu próprio commit para evitar uma transação
    gigante que bloqueie o banco durante os ~3.500 inserts de snapshot.
    """
    if not result.snapshots and not result.sale_history:
        log.warning("load() chamado com TransformResult vazio — nada a inserir.")
        return

    t0 = time.perf_counter()

    _insert_snapshots(db, result.snapshots)
    _insert_sale_history(db, result.sale_history)
    _upsert_retainer_active(db, result.retainer_active)
    _insert_inferred_sales(db, result.retainer_inferred)

    # Remove da my_active_listings as listagens inferidas como vendidas
    sold_ids = [
        r["listing_id"] for r in result.retainer_inferred
        if r.get("listing_id") is not None
    ]
    if sold_ids:
        _remove_sold_listings(db, sold_ids)

    elapsed = time.perf_counter() - t0
    log.info(f"Load concluído em {elapsed:.2f}s — {result.summary}")


# ---------------------------------------------------------------------------
# Operações por tabela
# ---------------------------------------------------------------------------

def _insert_snapshots(db: Session, snapshots: list[dict]) -> None:
    if not snapshots:
        return

    inserted = 0
    for chunk in _chunks(snapshots, CHUNK):
        r = db.execute(
            text("""
                INSERT INTO market_snapshots (
                    item_id, collected_at,
                    min_price_nq, min_price_hq,
                    avg_price_nq, avg_price_hq,
                    listings_count_nq, listings_count_hq
                ) VALUES (
                    :item_id, :collected_at,
                    :min_price_nq, :min_price_hq,
                    :avg_price_nq, :avg_price_hq,
                    :listings_count_nq, :listings_count_hq
                )
                ON CONFLICT (item_id, collected_at) DO NOTHING
            """),
            chunk,
        )
        inserted += r.rowcount
    db.commit()

    skipped = len(snapshots) - inserted
    log.debug(
        f"Snapshots: {inserted:,} inseridos | {skipped:,} já existiam."
    )


def _insert_sale_history(db: Session, sales: list[dict]) -> None:
    """
    buyer_name NULL → '' antes do INSERT.

    Motivo: PostgreSQL considera NULL != NULL em UNIQUE CONSTRAINTs.
    Sem essa normalização, duas vendas sem buyer_name no mesmo instante
    não seriam detectadas como duplicatas pelo ON CONFLICT.
    """
    if not sales:
        return

    normalized = [
        {**s, "buyer_name": s.get("buyer_name") or ""}
        for s in sales
    ]

    inserted = 0
    for chunk in _chunks(normalized, CHUNK):
        r = db.execute(
            text("""
                INSERT INTO sale_history (
                    item_id, sold_at, price_per_unit,
                    quantity, is_hq, buyer_name, upload_time
                ) VALUES (
                    :item_id, :sold_at, :price_per_unit,
                    :quantity, :is_hq, :buyer_name, :upload_time
                )
                ON CONFLICT (item_id, sold_at, price_per_unit, quantity, is_hq, buyer_name)
                DO NOTHING
            """),
            chunk,
        )
        inserted += r.rowcount
    db.commit()

    skipped = len(sales) - inserted
    log.debug(
        f"Sale history: {inserted:,} inseridos | {skipped:,} duplicatas ignoradas."
    )


def _upsert_retainer_active(db: Session, listings: list[dict]) -> None:
    """
    UPSERT em my_active_listings.

    - Nova listagem   → INSERT com first_seen_at = now
    - Listagem ativa  → UPDATE apenas last_seen_at, price e quantity
      (price pode ter mudado se o jogador ajustou o preço)
    """
    if not listings:
        return

    for chunk in _chunks(listings, CHUNK):
        db.execute(
            text("""
                INSERT INTO my_active_listings (
                    retainer_id, item_id, price_per_unit,
                    quantity, is_hq, first_seen_at, last_seen_at
                ) VALUES (
                    :retainer_id, :item_id, :price_per_unit,
                    :quantity, :is_hq, :first_seen_at, :last_seen_at
                )
                ON CONFLICT (retainer_id, item_id, is_hq) DO UPDATE SET
                    last_seen_at   = EXCLUDED.last_seen_at,
                    price_per_unit = EXCLUDED.price_per_unit,
                    quantity       = EXCLUDED.quantity
            """),
            chunk,
        )
    db.commit()
    log.debug(f"Retainer listings: {len(listings)} upserted.")


def _insert_inferred_sales(db: Session, inferred: list[dict]) -> None:
    if not inferred:
        return

    db.execute(
        text("""
            INSERT INTO my_inferred_sales (
                retainer_id, item_id, listing_id,
                estimated_sold_at, price_per_unit, quantity, is_hq
            ) VALUES (
                :retainer_id, :item_id, :listing_id,
                :estimated_sold_at, :price_per_unit, :quantity, :is_hq
            )
        """),
        inferred,
    )
    db.commit()
    log.info(f"Vendas inferidas: {len(inferred)} inseridas.")


def _remove_sold_listings(db: Session, listing_ids: list[int]) -> None:
    """
    Remove de my_active_listings as entradas que foram inferidas como vendidas.
    Usa SQLAlchemy ORM delete com in_() — limpo e seguro para listas dinâmicas.
    """
    stmt = delete(MyActiveListing).where(MyActiveListing.id.in_(listing_ids))
    result = db.execute(stmt)
    db.commit()
    log.debug(f"Listagens removidas após venda: {result.rowcount}.")