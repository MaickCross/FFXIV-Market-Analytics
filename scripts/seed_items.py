#!/usr/bin/env python3
"""
scripts/seed_items.py

Popula o catálogo de itens (item_category, item_subcategory, items)
cruzando dados de duas fontes:
  - Universalis API  →  lista de todos os IDs negociáveis no market board
  - XIVAPI v2        →  nome, categoria, flag HQ, ícone de cada item

Seguro para re-executar: usa INSERT ... ON CONFLICT DO NOTHING em todas
as tabelas — nunca duplica nem sobrescreve dados existentes.

Uso:
    python scripts/seed_items.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Garante que src/ seja encontrado independente de onde o script é chamado
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import text

from src.config import settings
from src.db.models import Base, Item, ItemCategory, ItemSubcategory
from src.db.session import SessionLocal, engine

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
XIVAPI_BASE = "https://v2.xivapi.com"
XIVAPI_FIELDS = ",".join([
    "Name",
    "ItemUICategory.Name",
    "ItemSearchCategory.Name",
    "ItemSearchCategory@as(raw)",  # 0 = não negociável
    "Icon",                         # retorna path do asset
    "CanBeHq",
])
BATCH_SIZE   = 100   # IDs por chamada ao XIVAPI (max recomendado = 100)
CONCURRENCY  = 5     # chamadas paralelas simultâneas ao XIVAPI(max recomendado = 5)
TIMEOUT      = 60.0  # segundos por requisição(minimo recomendado = 30)

# ---------------------------------------------------------------------------
# Mapeamento: ItemSearchCategory.Name → categoria principal (nossa)
# Itens não listados aqui caem automaticamente em "Items"
# ---------------------------------------------------------------------------
CATEGORY_MAP: dict[str, str] = {
    # --- Main Arm / Off Arm ---
    "Gladiator's Arm":              "Main Arm/Off Arm",
    "Pugilist's Arm":               "Main Arm/Off Arm",
    "Marauder's Arm":               "Main Arm/Off Arm",
    "Lancer's Arm":                 "Main Arm/Off Arm",
    "Archer's Arm":                 "Main Arm/Off Arm",
    "Rogue's Arm":                  "Main Arm/Off Arm",
    "Conjurer's Arm":               "Main Arm/Off Arm",
    "Thaumaturge's Arm":            "Main Arm/Off Arm",
    "Arcanist's Grimoire":          "Main Arm/Off Arm",
    "Astrologian's Arm":            "Main Arm/Off Arm",
    "Machinist's Arm":              "Main Arm/Off Arm",
    "Dark Knight's Arm":            "Main Arm/Off Arm",
    "Samurai's Arm":                "Main Arm/Off Arm",
    "Red Mage's Arm":               "Main Arm/Off Arm",
    "Gunbreaker's Arm":             "Main Arm/Off Arm",
    "Dancer's Arm":                 "Main Arm/Off Arm",
    "Reaper's Arm":                 "Main Arm/Off Arm",
    "Sage's Arm":                   "Main Arm/Off Arm",
    "Pictomancer's Arm":            "Main Arm/Off Arm",
    "Viper's Arm":                  "Main Arm/Off Arm",
    "Shield":                       "Main Arm/Off Arm",
    "Carpenter's Primary Tool":     "Main Arm/Off Arm",
    "Carpenter's Secondary Tool":   "Main Arm/Off Arm",
    "Blacksmith's Primary Tool":    "Main Arm/Off Arm",
    "Blacksmith's Secondary Tool":  "Main Arm/Off Arm",
    "Armorer's Primary Tool":       "Main Arm/Off Arm",
    "Armorer's Secondary Tool":     "Main Arm/Off Arm",
    "Goldsmith's Primary Tool":     "Main Arm/Off Arm",
    "Goldsmith's Secondary Tool":   "Main Arm/Off Arm",
    "Leatherworker's Primary Tool": "Main Arm/Off Arm",
    "Leatherworker's Secondary Tool": "Main Arm/Off Arm",
    "Weaver's Primary Tool":        "Main Arm/Off Arm",
    "Weaver's Secondary Tool":      "Main Arm/Off Arm",
    "Alchemist's Primary Tool":     "Main Arm/Off Arm",
    "Alchemist's Secondary Tool":   "Main Arm/Off Arm",
    "Culinarian's Primary Tool":    "Main Arm/Off Arm",
    "Culinarian's Secondary Tool":  "Main Arm/Off Arm",
    "Miner's Primary Tool":         "Main Arm/Off Arm",
    "Miner's Secondary Tool":       "Main Arm/Off Arm",
    "Botanist's Primary Tool":      "Main Arm/Off Arm",
    "Botanist's Secondary Tool":    "Main Arm/Off Arm",
    "Fisher's Primary Tool":        "Main Arm/Off Arm",
    # --- Armor ---
    "Head":    "Armor",
    "Body":    "Armor",
    "Hands":   "Armor",
    "Waist":   "Armor",
    "Legs":    "Armor",
    "Feet":    "Armor",
    "Neck":    "Armor",
    "Ears":    "Armor",
    "Wrists":  "Armor",
    "Ring":    "Armor",
    # --- Housing ---
    "Outdoor Furnishing":  "Housing",
    "Tabletop":            "Housing",
    "Interior Furnishing": "Housing",
    "Ceiling Furnishing":  "Housing",
    "Wall Furnishing":     "Housing",
    "Window Furnishing":   "Housing",
    "Door Furnishing":     "Housing",
    "Rug":                 "Housing",
    "Gardening":           "Housing",
    "Painting":            "Housing",
    "Orchestrion Roll":    "Housing",
    # Tudo mais → "Items" (fallback implícito)
}


# ---------------------------------------------------------------------------
# Etapa 1 — Busca IDs negociáveis na Universalis
# ---------------------------------------------------------------------------

async def fetch_marketable_ids(client: httpx.AsyncClient) -> list[int]:
    """Retorna todos os item_ids negociáveis no market board do Behemoth."""
    log.info("Buscando IDs de itens negociáveis na Universalis...")
    resp = await client.get(
        f"{settings.UNIVERSALIS_BASE_URL}/marketable",
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    ids: list[int] = resp.json()
    log.info(f"  → {len(ids):,} itens negociáveis encontrados.")
    return ids


# ---------------------------------------------------------------------------
# Etapa 2 — Busca metadados no XIVAPI v2
# ---------------------------------------------------------------------------

async def _fetch_batch(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    batch: list[int],
    batch_index: int,
    total_batches: int,
) -> list[dict]:
    """Busca um lote de itens no XIVAPI v2 usando o parâmetro `rows`."""
    async with semaphore:
        resp = await client.get(
            f"{XIVAPI_BASE}/api/sheet/Item",
            params={"rows": ",".join(str(i) for i in batch), "fields": XIVAPI_FIELDS},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        if batch_index % 5 == 0 or batch_index == total_batches:
            log.info(f"  → XIVAPI: {batch_index}/{total_batches} lotes concluídos")
        return rows


async def fetch_all_item_metadata(item_ids: list[int]) -> list[dict]:
    """Divide os IDs em lotes e busca todos em paralelo no XIVAPI v2."""
    batches = [item_ids[i: i + BATCH_SIZE] for i in range(0, len(item_ids), BATCH_SIZE)]
    total = len(batches)
    log.info(f"Buscando metadados de {len(item_ids):,} itens em {total} lotes no XIVAPI v2...")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    headers = {"User-Agent": "ffxiv-market-tracker/1.0 (personal project)"}

    async with httpx.AsyncClient(headers=headers) as client:
        tasks = [
            _fetch_batch(client, semaphore, batch, i + 1, total)
            for i, batch in enumerate(batches)
        ]
        results: list[dict] = []
        for coro in asyncio.as_completed(tasks):
            rows = await coro
            results.extend(rows)

    log.info(f"  → {len(results):,} linhas brutas recebidas do XIVAPI.")
    return results


# ---------------------------------------------------------------------------
# Etapa 3 — Parsing de cada linha bruta
# ---------------------------------------------------------------------------

def _build_icon_url(icon_field: dict | None) -> str | None:
    """
    Constrói a URL pública do ícone a partir do campo Icon retornado pelo XIVAPI v2.
    Exemplo de path_hr1: 'ui/icon/055000/055487_hr1.tex'
    URL final: https://v2.xivapi.com/api/asset?path=ui/icon/...&format=png
    """
    if not icon_field or not isinstance(icon_field, dict):
        return None
    path = icon_field.get("path_hr1") or icon_field.get("path")
    if not path:
        return None
    return f"{XIVAPI_BASE}/api/asset?path={path}&format=png"


def parse_item_row(row: dict) -> dict | None:
    """
    Converte uma linha bruta do XIVAPI em dict pronto para o banco.
    Retorna None se o item deve ser ignorado (sem nome, não negociável).
    """
    item_id: int | None = row.get("row_id")
    fields: dict = row.get("fields", {})

    name = (fields.get("Name") or "").strip()
    if not name or not item_id:
        return None

    # Filtra itens que não são negociáveis no market board
    search_cat_raw = fields.get("ItemSearchCategory@as(raw)", 0)
    if not search_cat_raw:
        return None

    # Nome da subcategoria de busca (ex: "Interior Furnishing")
    search_cat_name = (
        (fields.get("ItemSearchCategory") or {})
        .get("fields", {})
        .get("Name", "")
        .strip()
    ) or "Miscellaneous"

    # Nome da categoria de UI do item (mais granular, ex: "Stone")
    ui_cat_name = (
        (fields.get("ItemUICategory") or {})
        .get("fields", {})
        .get("Name", "")
        .strip()
    ) or search_cat_name

    category_name = CATEGORY_MAP.get(search_cat_name, "Items")
    can_be_hq = bool(fields.get("CanBeHq", False))
    icon_url = _build_icon_url(fields.get("Icon"))

    return {
        "item_id":    item_id,
        "name":       name,
        "category":   category_name,
        "subcategory": ui_cat_name,
        "can_be_hq":  can_be_hq,
        "icon_url":   icon_url,
    }


# ---------------------------------------------------------------------------
# Etapa 4 — Persistência no banco
# ---------------------------------------------------------------------------

def seed_database(parsed_items: list[dict]) -> None:
    """
    Insere categorias, subcategorias e itens no banco de dados.
    Usa INSERT ... ON CONFLICT DO NOTHING em todas as etapas:
    seguro para re-execução sem duplicatas.
    """
    db = SessionLocal()
    try:
        _insert_categories(db, parsed_items)
        cat_id_map   = _load_category_map(db)
        _insert_subcategories(db, parsed_items, cat_id_map)
        subcat_id_map = _load_subcategory_map(db)
        _insert_items(db, parsed_items, cat_id_map, subcat_id_map)

        total = db.execute(text("SELECT COUNT(*) FROM items")).scalar()
        log.info(f"✓ Seed concluído com sucesso. Total de itens no banco: {total:,}")

    except Exception:
        db.rollback()
        log.exception("Erro durante o seed — rollback executado.")
        raise
    finally:
        db.close()


def _insert_categories(db, items: list[dict]) -> None:
    unique_cats = {p["category"] for p in items}
    log.info(f"Inserindo {len(unique_cats)} categorias...")
    db.execute(
        text("""
            INSERT INTO item_category (name)
            VALUES (:name)
            ON CONFLICT (name) DO NOTHING
        """),
        [{"name": n} for n in sorted(unique_cats)],
    )
    db.commit()


def _load_category_map(db) -> dict[str, int]:
    rows = db.execute(text("SELECT id, name FROM item_category")).fetchall()
    return {row.name: row.id for row in rows}


def _insert_subcategories(db, items: list[dict], cat_id_map: dict[str, int]) -> None:
    unique_pairs = {(p["category"], p["subcategory"]) for p in items}
    log.info(f"Inserindo {len(unique_pairs)} subcategorias...")
    db.execute(
        text("""
            INSERT INTO item_subcategory (category_id, name)
            VALUES (:category_id, :name)
            ON CONFLICT (category_id, name) DO NOTHING
        """),
        [
            {"category_id": cat_id_map[cat], "name": subcat}
            for cat, subcat in sorted(unique_pairs)
            if cat in cat_id_map
        ],
    )
    db.commit()


def _load_subcategory_map(db) -> dict[tuple[int, str], int]:
    """Retorna {(category_id, subcat_name): subcat_id}."""
    rows = db.execute(
        text("SELECT id, category_id, name FROM item_subcategory")
    ).fetchall()
    return {(row.category_id, row.name): row.id for row in rows}


def _insert_items(
    db,
    items: list[dict],
    cat_id_map: dict[str, int],
    subcat_id_map: dict[tuple[int, str], int],
) -> None:
    log.info(f"Inserindo {len(items):,} itens...")
    CHUNK = 500
    skipped = 0
    inserted_total = 0

    for offset in range(0, len(items), CHUNK):
        chunk = items[offset: offset + CHUNK]
        values = []
        for p in chunk:
            cat_id = cat_id_map.get(p["category"])
            if cat_id is None:
                skipped += 1
                continue
            subcat_id = subcat_id_map.get((cat_id, p["subcategory"]))
            if subcat_id is None:
                skipped += 1
                continue
            values.append({
                "item_id":       p["item_id"],
                "name":          p["name"],
                "subcategory_id": subcat_id,
                "can_be_hq":     p["can_be_hq"],
                "icon_url":      p["icon_url"],
            })

        if values:
            db.execute(
                text("""
                    INSERT INTO items (item_id, name, subcategory_id, can_be_hq, icon_url)
                    VALUES (:item_id, :name, :subcategory_id, :can_be_hq, :icon_url)
                    ON CONFLICT (item_id) DO NOTHING
                """),
                values,
            )
            inserted_total += len(values)

    db.commit()

    if skipped:
        log.warning(f"  ⚠ {skipped} itens ignorados (categoria/subcategoria não encontrada).")
    log.info(f"  → {inserted_total:,} itens processados.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    # Garante que as tabelas existem (útil em dev sem rodar alembic manualmente)
    Base.metadata.create_all(bind=engine)

    async with httpx.AsyncClient() as client:
        item_ids = await fetch_marketable_ids(client)

    raw_rows = await fetch_all_item_metadata(item_ids)

    log.info("Parseando metadados...")
    parsed = [p for row in raw_rows if (p := parse_item_row(row)) is not None]
    skipped = len(raw_rows) - len(parsed)
    log.info(f"  → {len(parsed):,} válidos, {skipped:,} ignorados (sem nome ou não negociáveis).")

    seed_database(parsed)


if __name__ == "__main__":
    asyncio.run(main())