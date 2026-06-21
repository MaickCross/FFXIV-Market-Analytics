
#Comanda o pipeline ETL completo e agenda execuções automáticas.

#Fluxo de uma execução:
#  1. fetch_pipeline_context(db)   > lê item IDs, retainers e listagens ativas
#  2. extract(item_ids)            > busca dados da Universalis (async)
#  3. transform(...)               > converte dados brutos em registros tipados
#  4. load(db, result)             > persiste no banco

#O scheduler roda a primeira coleta imediatamente ao iniciar e depois
#repete a cada ETL_INTERVAL_HOURS (definido no .env, padrão: 1h).
#max_instances=1 garante que uma coleta em andamento não seja sobreposta
#pela próxima se o ETL demorar mais que o intervalo configurado.

#Uso: python src/scheduler.py


import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import settings
#from src.db.session import get_session
from src.db.session import SessionLocal
from src.db.session import SessionLocal
from src.etl.extractor import extract
from src.etl.loader import fetch_pipeline_context, load
from src.etl.transformer import transform

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_etl_pipeline() -> None:
    
    #Executa uma coleta completa do mercado.
    #Chamada pelo scheduler a cada intervalo configurado.
    #Exceções são capturadas e logadas para não derrubar o scheduler.
    
    collected_at = datetime.now(tz=timezone.utc)
    log.info(f"{'='*55}")
    log.info(f"  ETL iniciado: {collected_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log.info(f"{'='*55}")

    try:
        _run(collected_at)
    except Exception:
        log.exception("Erro durante o pipeline ETL. Próxima coleta será tentada normalmente.")


def _run(collected_at: datetime) -> None:
    # ── Etapa 1: contexto pré-transformação ──────────────────────────────
    #with get_session() as db:
        #context = fetch_pipeline_context(db)

    with SessionLocal() as db:
        context = fetch_pipeline_context(db)

    if not context["known_item_ids"]:
        log.error(
            "Catálogo de itens vazio. "
            "Execute 'python scripts/seed_items.py' antes de iniciar o scheduler."
        )
        return

    # ── Etapa 2: extração (async) ─────────────────────────────────────────
    item_ids = list(context["known_item_ids"])
    extract_result = asyncio.run(extract(item_ids))

    if not extract_result.listings_raw:
        log.warning("Extração retornou sem dados de listings. Coleta abortada.")
        return

    # ── Etapa 3: transformação ────────────────────────────────────────────
    transform_result = transform(
        result=extract_result,
        collected_at=collected_at,
        retainer_map=context["retainer_map"],
        known_item_ids=context["known_item_ids"],
        previous_active=context["previous_active"],
    )

    # ── Etapa 4: persistência ─────────────────────────────────────────────
    #with get_session() as db:
        #load(db, transform_result)

    with SessionLocal() as db:
        load(db, transform_result)

    log.info("ETL concluído com sucesso.")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_etl_pipeline,
        trigger=IntervalTrigger(hours=settings.ETL_INTERVAL_HOURS),
        id="etl_pipeline",
        name="FFXIV Market ETL",
        replace_existing=True,
        max_instances=1,        # impede sobreposição se ETL demorar > 1h
        misfire_grace_time=300, # tolera 5min de atraso antes de pular execução
    )

    log.info(f"Servidor: {settings.WORLD}")
    log.info(f"Intervalo de coleta: {settings.ETL_INTERVAL_HOURS}h")
    log.info(f"Retenção de dados: {settings.RETENTION_DAYS} dias")
    log.info("Executando primeira coleta imediatamente...")

    # Roda a primeira coleta antes de bloquear no scheduler
    run_etl_pipeline()

    log.info(f"Aguardando próxima execução em {settings.ETL_INTERVAL_HOURS}h...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler encerrado pelo usuário.")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()