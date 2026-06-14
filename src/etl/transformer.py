"""
src/etl/transformer.py

Responsabilidade única: converter os dados brutos do ExtractResult
em registros tipados e prontos para inserção no banco.

Regras desta camada:
  - Sem I/O (sem banco, sem rede)
  - Sem efeitos colaterais
  - Recebe dados → devolve dados
  - Toda lógica de negócio fica aqui (cálculos, inferências, filtros)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.etl.extractor import ExtractResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resultado da transformação
# ---------------------------------------------------------------------------

@dataclass
class TransformResult:
    """
    Container com todos os registros prontos para o loader inserir no banco.
    Cada lista corresponde a uma tabela.
    """
    # market_snapshots: snapshot agregado por item neste instante
    snapshots: list[dict] = field(default_factory=list)

    # sale_history: cada venda concluída detectada (com dedup pelo banco)
    sale_history: list[dict] = field(default_factory=list)

    # my_active_listings: listagens atuais dos meus retainers (para UPSERT)
    retainer_active: list[dict] = field(default_factory=list)

    # my_inferred_sales: vendas inferidas (listagem sumiu entre coletas)
    retainer_inferred: list[dict] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"snapshots={len(self.snapshots):,} | "
            f"sales={len(self.sale_history):,} | "
            f"retainer_listings={len(self.retainer_active)} | "
            f"inferred_sales={len(self.retainer_inferred)}"
        )


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def transform(
    result: ExtractResult,
    collected_at: datetime,
    retainer_map: dict[str, int],
    known_item_ids: frozenset[int],
    previous_active: list[dict],
) -> TransformResult:
    """
    Transforma os dados brutos do extractor em registros prontos para o banco.

    Args:
        result:          Dados brutos do ExtractResult (listings + history).
        collected_at:    Timestamp desta coleta (definido pelo scheduler).
        retainer_map:    {retainer_name: retainer_id} — meus retainers cadastrados.
        known_item_ids:  Conjunto de item_ids presentes na tabela items do banco.
        previous_active: Linhas atuais de my_active_listings (para inferência de vendas).

    Returns:
        TransformResult com listas de dicts prontos para INSERT.
    """
    if not result.listings_raw and not result.history_raw:
        log.warning("TransformResult vazio: ExtractResult não contém dados.")
        return TransformResult()

    # Normaliza retainer names para comparação case-insensitive
    retainer_lookup: dict[str, int] = {
        name.lower(): rid for name, rid in retainer_map.items()
    }

    out = TransformResult()

    out.snapshots = _build_snapshots(
        result.listings_raw, collected_at, known_item_ids
    )
    out.sale_history = _build_sale_history(
        result.history_raw, result.listings_raw, known_item_ids
    )
    out.retainer_active, out.retainer_inferred = _process_retainer_listings(
        result.listings_raw, collected_at, retainer_lookup, known_item_ids, previous_active
    )

    log.info(f"Transformação concluída: {out.summary}")
    return out


# ---------------------------------------------------------------------------
# Snapshots de mercado
# ---------------------------------------------------------------------------

def _build_snapshots(
    listings_raw: dict[int, dict],
    collected_at: datetime,
    known_item_ids: frozenset[int],
) -> list[dict]:
    """
    Constrói registros para market_snapshots a partir dos dados de listings.

    Usa os campos pré-calculados da Universalis (minPrice, currentAveragePrice)
    e conta NQ/HQ a partir do array de listings retornado (≤ MAX_LISTINGS).
    Preços 0 são convertidos para None (item sem listagens naquela qualidade).
    """
    snapshots = []
    skipped = 0

    for item_id, data in listings_raw.items():
        if item_id not in known_item_ids:
            skipped += 1
            continue

        listings = data.get("listings", [])
        nq_count = sum(1 for l in listings if not l.get("hq", False))
        hq_count = sum(1 for l in listings if l.get("hq", False))

        # Universalis retorna 0 quando não há listagens daquela qualidade
        def _price_or_none(val: float | int | None) -> float | None:
            return float(val) if val else None

        snapshots.append({
            "item_id":           item_id,
            "collected_at":      collected_at,
            "min_price_nq":      _price_or_none(data.get("minPriceNQ")),
            "min_price_hq":      _price_or_none(data.get("minPriceHQ")),
            "avg_price_nq":      _price_or_none(data.get("currentAveragePriceNQ")),
            "avg_price_hq":      _price_or_none(data.get("currentAveragePriceHQ")),
            "listings_count_nq": nq_count or None,
            "listings_count_hq": hq_count or None,
        })

    if skipped:
        log.debug(f"Snapshots: {skipped} itens ignorados (não estão no catálogo).")

    log.debug(f"Snapshots gerados: {len(snapshots):,}")
    return snapshots


# ---------------------------------------------------------------------------
# Histórico de vendas
# ---------------------------------------------------------------------------

def _ts_to_utc(timestamp_seconds: int | float) -> datetime:
    """Converte Unix timestamp (segundos) para datetime UTC."""
    return datetime.fromtimestamp(float(timestamp_seconds), tz=timezone.utc)


def _build_sale_history(
    history_raw: dict[int, dict],
    listings_raw: dict[int, dict],
    known_item_ids: frozenset[int],
) -> list[dict]:
    """
    Constrói registros para sale_history a partir do endpoint /history/.

    upload_time: usamos o lastUploadTime do item (nível de listings_raw) como
    proxy para quando a Universalis recebeu os dados desse item. Não há
    timestamp de upload por entrada individual no histórico.

    Deduplicação fica no banco via UNIQUE CONSTRAINT — aqui não filtramos.
    """
    records = []
    skipped = 0

    for item_id, hist_data in history_raw.items():
        if item_id not in known_item_ids:
            skipped += 1
            continue

        # upload_time como proxy de frescor do dado
        raw_upload = (listings_raw.get(item_id) or {}).get("lastUploadTime")
        upload_time: datetime | None = None
        if raw_upload:
            # lastUploadTime vem em milissegundos
            upload_time = _ts_to_utc(raw_upload / 1000)

        entries = hist_data.get("entries", [])
        for entry in entries:
            ts = entry.get("timestamp")
            price = entry.get("pricePerUnit")
            qty = entry.get("quantity")

            # Entradas malformadas são ignoradas silenciosamente
            if not ts or not price or not qty:
                continue

            records.append({
                "item_id":       item_id,
                "sold_at":       _ts_to_utc(ts),
                "price_per_unit": int(price),
                "quantity":       int(qty),
                "is_hq":          bool(entry.get("hq", False)),
                "buyer_name":     entry.get("buyerName") or None,
                "upload_time":    upload_time,
            })

    if skipped:
        log.debug(f"Sale history: {skipped} itens ignorados (não estão no catálogo).")

    log.debug(f"Registros de venda gerados: {len(records):,}")
    return records


# ---------------------------------------------------------------------------
# Listagens dos meus retainers + inferência de vendas
# ---------------------------------------------------------------------------

def _process_retainer_listings(
    listings_raw: dict[int, dict],
    collected_at: datetime,
    retainer_lookup: dict[str, int],
    known_item_ids: frozenset[int],
    previous_active: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Detecta listagens dos meus retainers no snapshot atual e infere vendas
    a partir de listagens que desapareceram desde a coleta anterior.

    Retorna:
        (retainer_active, retainer_inferred)

    retainer_active:   Registros para UPSERT em my_active_listings.
                       Preserva first_seen_at para listagens já conhecidas.

    retainer_inferred: Registros para INSERT em my_inferred_sales.
                       Gerado quando uma listagem prévia não aparece mais.

    Chave de identidade de uma listagem: (retainer_name_lower, item_id, is_hq)
    A Universalis não fornece um ID estável de listagem entre coletas.
    """

    # --- Detecta listagens atuais dos meus retainers ---
    # {(retainer_name_lower, item_id, is_hq): listing_dict}
    current: dict[tuple[str, int, bool], dict] = {}

    for item_id, item_data in listings_raw.items():
        if item_id not in known_item_ids:
            continue
        for listing in item_data.get("listings", []):
            rname: str = listing.get("retainerName", "")
            rname_lower = rname.lower()
            if rname_lower not in retainer_lookup:
                continue

            is_hq = bool(listing.get("hq", False))
            key = (rname_lower, item_id, is_hq)
            current[key] = {
                "retainer_id":   retainer_lookup[rname_lower],
                "item_id":       item_id,
                "price_per_unit": int(listing["pricePerUnit"]),
                "quantity":       int(listing["quantity"]),
                "is_hq":          is_hq,
                "first_seen_at":  collected_at,   # sobrescrito se já existia
                "last_seen_at":   collected_at,
                "_retainer_name": rname_lower,    # campo auxiliar, removido antes do INSERT
            }

    # --- Indexa o estado anterior por chave ---
    # previous_active vem do banco: cada row tem {id, retainer_id, item_id, is_hq,
    # price_per_unit, quantity, first_seen_at, retainer_name}
    previous: dict[tuple[str, int, bool], dict] = {}
    for row in previous_active:
        key = (row["retainer_name"].lower(), row["item_id"], bool(row["is_hq"]))
        previous[key] = row

    # --- Produz lista para UPSERT ---
    retainer_active: list[dict] = []
    for key, listing in current.items():
        if key in previous:
            # Listagem ainda ativa: preserva first_seen_at original
            listing = {**listing, "first_seen_at": previous[key]["first_seen_at"]}
        record = {k: v for k, v in listing.items() if not k.startswith("_")}
        retainer_active.append(record)

    # --- Infere vendas de listagens que sumiram ---
    retainer_inferred: list[dict] = []
    for key, prev_row in previous.items():
        if key in current:
            continue  # ainda ativa, nada a fazer

        # Listagem desapareceu → venda provável
        retainer_inferred.append({
            "retainer_id":      prev_row["retainer_id"],
            "item_id":          prev_row["item_id"],
            "listing_id":       prev_row.get("id"),       # my_active_listings.id
            "estimated_sold_at": collected_at,
            "price_per_unit":   prev_row["price_per_unit"],
            "quantity":         prev_row["quantity"],
            "is_hq":            prev_row["is_hq"],
        })

    if retainer_inferred:
        log.info(
            f"Retainers: {len(retainer_active)} listagens ativas | "
            f"{len(retainer_inferred)} vendas inferidas"
        )

    return retainer_active, retainer_inferred