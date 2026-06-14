#!/usr/bin/env python3
"""
scripts/purge.py

Remove dados mais antigos que o período de retenção configurado.
Deve ser executado diariamente — pelo scheduler ou por um cron job externo.

Uso:
    python scripts/purge.py              # usa RETENTION_DAYS do .env
    python scripts/purge.py --days 30   # sobrescreve o valor do .env
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config import settings
from src.db.session import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def purge(days: int) -> None:
    """
    Deleta registros mais antigos que `days` dias nas tabelas de séries temporais.
    Tabelas de catálogo (items, item_category, etc.) não são tocadas.
    """
    interval = f"{days} days"
    tables = [
        ("sale_history",      "sold_at",              "vendas"),
        ("market_snapshots",  "collected_at",         "snapshots"),
        ("my_inferred_sales", "estimated_sold_at",    "vendas inferidas"),
    ]

    log.info(f"Iniciando purge — removendo dados com mais de {days} dias...")
    total_deleted = 0

    db = SessionLocal()
    try:
        for table, col, label in tables:
            result = db.execute(
                text(f"""
                    DELETE FROM {table}
                    WHERE {col} < NOW() - CAST(:interval AS INTERVAL)
                """),
                {"interval": interval},
            )
            deleted = result.rowcount
            total_deleted += deleted
            log.info(f"  {label:20s} → {deleted:,} linhas removidas")

        db.commit()
        log.info(f"Purge concluído. Total removido: {total_deleted:,} linhas.")

    except Exception:
        db.rollback()
        log.exception("Erro durante o purge — rollback executado.")
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Purga dados antigos do banco.")
    parser.add_argument(
        "--days",
        type=int,
        default=settings.RETENTION_DAYS,
        help=f"Dias de retenção (padrão: {settings.RETENTION_DAYS} do .env)",
    )
    args = parser.parse_args()
    purge(args.days)


if __name__ == "__main__":
    main()