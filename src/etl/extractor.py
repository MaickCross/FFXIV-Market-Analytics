"""
src/etl/extractor.py

Responsabilidade única: buscar dados brutos da Universalis API.
Sem transformação, sem lógica de negócio, sem acesso ao banco.

Dois endpoints chamados por batch de itens:
  /api/v2/Behemoth/{ids}         → listings ativos + dados de snapshot
  /api/v2/history/Behemoth/{ids} → histórico de vendas concluídas

Os dois tipos de chamada rodam em paralelo, controlados por um
asyncio.Semaphore compartilhado para respeitar o rate limit da API.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from src.config import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
BATCH_SIZE       = 100   # máximo de IDs por requisição (limite Universalis)
HISTORY_ENTRIES  = 500   # entradas de histórico por item (última N vendas)
MAX_LISTINGS     = 20    # listings por item — suficiente para detectar retainers
TIMEOUT          = 30.0  # segundos por requisição
_HEADERS = {"User-Agent": "ffxiv-market-tracker/1.0 (personal project)"}


# ---------------------------------------------------------------------------
# Resultado da extração
# ---------------------------------------------------------------------------

@dataclass
class ExtractResult:
    """
    Container com todos os dados brutos de uma execução do ETL.

    listings_raw: item_id → payload do endpoint /api/v2/Behemoth/{ids}
                  Contém: listings[], minPriceNQ/HQ, currentAveragePriceNQ/HQ, etc.

    history_raw:  item_id → payload do endpoint /api/v2/history/Behemoth/{ids}
                  Contém: entries[] com timestamp, price, quantity, buyerName, hq.
    """
    listings_raw: dict[int, dict] = field(default_factory=dict)
    history_raw:  dict[int, dict] = field(default_factory=dict)

    @property
    def total_items(self) -> int:
        return len(self.listings_raw)

    @property
    def failed_items(self) -> set[int]:
        """IDs que faltaram em qualquer um dos dois endpoints."""
        return set(self.listings_raw) ^ set(self.history_raw)


# ---------------------------------------------------------------------------
# Funções de fetch por batch
# ---------------------------------------------------------------------------

async def _fetch_listings_batch(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    batch: list[int],
) -> tuple[list[int], dict | Exception]:
    """
    Busca listings e dados de snapshot para um batch de itens.
    Retorna (batch, response_dict) ou (batch, Exception) em caso de erro.
    """
    ids_str = ",".join(str(i) for i in batch)
    url = f"{settings.UNIVERSALIS_BASE_URL}/{settings.WORLD}/{ids_str}"
    params = {
        "listings": MAX_LISTINGS,
        "entries": 0,       # histórico vem do endpoint dedicado
    }
    async with semaphore:
        try:
            resp = await client.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return batch, resp.json()
        except Exception as exc:
            return batch, exc


async def _fetch_history_batch(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    batch: list[int],
) -> tuple[list[int], dict | Exception]:
    """
    Busca histórico de vendas para um batch de itens.
    Retorna (batch, response_dict) ou (batch, Exception) em caso de erro.
    """
    ids_str = ",".join(str(i) for i in batch)
    url = f"{settings.UNIVERSALIS_BASE_URL}/history/{settings.WORLD}/{ids_str}"
    params = {"entriesToReturn": HISTORY_ENTRIES}
    async with semaphore:
        try:
            resp = await client.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return batch, resp.json()
        except Exception as exc:
            return batch, exc


# ---------------------------------------------------------------------------
# Normalização de resposta (single-item vs multi-item)
# ---------------------------------------------------------------------------

def _normalize_response(batch: list[int], payload: dict) -> dict[int, dict]:
    """
    A Universalis retorna formatos diferentes dependendo do número de IDs:
      - 1 ID  → o payload É o item diretamente (sem wrapper 'items')
      - N IDs → {"items": {"<id>": {...}, ...}}

    Esta função normaliza para sempre retornar dict[int, dict].
    """
    if len(batch) == 1:
        item_id = batch[0]
        # Garante que o itemID bate com o que pedimos
        if payload.get("itemID") == item_id or "minPriceNQ" in payload or "entries" in payload:
            return {item_id: payload}
        return {}

    items_dict = payload.get("items", {})
    return {int(k): v for k, v in items_dict.items() if v}


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

async def extract(item_ids: list[int]) -> ExtractResult:
    """
    Extrai listings e histórico de todos os item_ids fornecidos.

    Lança asyncio.gather com listings + history em paralelo.
    Batches com erro são logados e ignorados (resultado parcial é válido).
    """
    if not item_ids:
        log.warning("extract() chamado com lista vazia de item_ids.")
        return ExtractResult()

    batches = [
        item_ids[i: i + BATCH_SIZE]
        for i in range(0, len(item_ids), BATCH_SIZE)
    ]
    total_batches = len(batches)
    log.info(
        f"Iniciando extração: {len(item_ids):,} itens | "
        f"{total_batches} batches | semáforo={settings.API_CONCURRENCY}"
    )

    semaphore = asyncio.Semaphore(settings.API_CONCURRENCY)
    result = ExtractResult()

    async with httpx.AsyncClient(headers=_HEADERS) as client:
        # Lança TODOS os tasks (listings + history) em paralelo.
        # O semáforo garante no máximo API_CONCURRENCY requisições simultâneas.
        listings_tasks = [
            _fetch_listings_batch(client, semaphore, batch)
            for batch in batches
        ]
        history_tasks = [
            _fetch_history_batch(client, semaphore, batch)
            for batch in batches
        ]

        all_responses = await asyncio.gather(
            *listings_tasks,
            *history_tasks,
            return_exceptions=False,  # os erros são capturados internamente
        )

    listings_responses = all_responses[:total_batches]
    history_responses  = all_responses[total_batches:]

    # --- Processa listings ---
    listings_errors = 0
    for batch, payload in listings_responses:
        if isinstance(payload, Exception):
            log.warning(f"Erro no batch de listings {batch[:3]}...: {payload}")
            listings_errors += 1
            continue
        result.listings_raw.update(_normalize_response(batch, payload))

    # --- Processa history ---
    history_errors = 0
    for batch, payload in history_responses:
        if isinstance(payload, Exception):
            log.warning(f"Erro no batch de history {batch[:3]}...: {payload}")
            history_errors += 1
            continue
        result.history_raw.update(_normalize_response(batch, payload))

    # --- Relatório final ---
    log.info(
        f"Extração concluída: "
        f"listings={len(result.listings_raw):,} itens "
        f"({listings_errors} batches com erro) | "
        f"history={len(result.history_raw):,} itens "
        f"({history_errors} batches com erro)"
    )
    if result.failed_items:
        log.warning(
            f"{len(result.failed_items)} itens com dados incompletos "
            f"(presentes em apenas um endpoint). Serão ignorados no transformer."
        )

    return result