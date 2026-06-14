"""
src/dashboard/queries.py

Funções de consulta ao banco para o dashboard.
Cada função retorna um pandas DataFrame já formatado para exibição,
ou um dict de métricas simples para os cards de KPI.

Sem lógica de apresentação aqui — apenas SQL + leve formatação de tipos.
"""

import logging

import pandas as pd
from sqlalchemy import text

from src.db.session import SessionLocal

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _query(sql: str, params: dict | None = None) -> list:
    """Executa uma query e retorna as linhas. Gerencia a sessão internamente."""
    db = SessionLocal()
    try:
        rows = db.execute(text(sql), params or {}).fetchall()
        return rows
    except Exception:
        log.exception("Erro ao executar query no dashboard.")
        return []
    finally:
        db.close()


def _fmt_gil(value) -> str:
    """Formata um valor de gil para exibição compacta."""
    if value is None:
        return "—"
    v = int(value)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    return f"{v:,}"


# ---------------------------------------------------------------------------
# Consultas de mercado
# ---------------------------------------------------------------------------

def get_categories() -> list[str]:
    """Retorna todas as categorias de itens cadastradas."""
    rows = _query("SELECT name FROM item_category ORDER BY name")
    return [r[0] for r in rows]


def get_top_items(days: int = 7, category: str | None = None) -> pd.DataFrame:
    """
    Top itens por rotatividade (gil/dia) no período especificado.
    category=None retorna todas as categorias.
    """
    rows = _query(
        """
        SELECT
            i.item_id,
            i.name                                                  AS item_name,
            ic.name                                                 AS category,
            isc.name                                                AS subcategory,
            ROUND(
                SUM(sh.quantity * sh.price_per_unit)::numeric / :days_f
            )                                                       AS gil_per_day,
            ROUND(COUNT(sh.id)::numeric / :days_f, 1)               AS sales_per_day,
            ROUND(AVG(sh.price_per_unit))                           AS avg_price,
            SUM(sh.quantity)                                        AS total_units,
            COUNT(sh.id)                                            AS total_sales
        FROM sale_history sh
        JOIN items            i   ON i.item_id    = sh.item_id
        JOIN item_subcategory isc ON isc.id        = i.subcategory_id
        JOIN item_category    ic  ON ic.id         = isc.category_id
        WHERE sh.sold_at >= NOW() - CAST(:interval AS INTERVAL)
          AND (:category IS NULL OR ic.name = :category)
        GROUP BY i.item_id, i.name, ic.name, isc.name
        HAVING COUNT(sh.id) >= 2
        ORDER BY gil_per_day DESC
        LIMIT 300
        """,
        {
            "days_f":   float(days),
            "interval": f"{days} days",
            "category": category,
        },
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "item_id", "item_name", "category", "subcategory",
        "gil_per_day", "sales_per_day", "avg_price",
        "total_units", "total_sales",
    ])
    # Formata para exibição (mantém colunas originais para callbacks)
    df["gil_per_day_fmt"]   = df["gil_per_day"].apply(_fmt_gil)
    df["avg_price_fmt"]     = df["avg_price"].apply(_fmt_gil)
    df["sales_per_day_fmt"] = df["sales_per_day"].apply(
        lambda x: f"{float(x):.1f}" if x is not None else "—"
    )
    df["total_units_fmt"] = df["total_units"].apply(
        lambda x: f"{int(x):,}" if x is not None else "—"
    )
    return df


def get_price_history(item_id: int, days: int = 14) -> pd.DataFrame:
    """
    Histórico de snapshots de preço para um item específico.
    Usado para o gráfico de tendência ao selecionar um item.
    """
    rows = _query(
        """
        SELECT
            collected_at,
            min_price_nq,
            min_price_hq,
            avg_price_nq,
            avg_price_hq
        FROM market_snapshots
        WHERE item_id  = :item_id
          AND collected_at >= NOW() - CAST(:interval AS INTERVAL)
        ORDER BY collected_at ASC
        """,
        {"item_id": item_id, "interval": f"{days} days"},
    )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=[
        "collected_at", "min_price_nq", "min_price_hq",
        "avg_price_nq", "avg_price_hq",
    ])


def get_last_collection() -> str:
    """Retorna o timestamp da última coleta formatado para exibição."""
    rows = _query("SELECT MAX(collected_at) FROM market_snapshots")
    val = rows[0][0] if rows else None
    if not val:
        return "Nenhuma coleta ainda"
    return val.strftime("%d/%m/%Y %H:%M UTC")


def get_market_kpis(days: int = 7) -> dict:
    """KPIs gerais do mercado para o período especificado."""
    rows = _query(
        """
        SELECT
            COUNT(DISTINCT item_id)                       AS itens_ativos,
            COUNT(*)                                      AS total_vendas,
            COALESCE(SUM(quantity * price_per_unit), 0)   AS volume_total
        FROM sale_history
        WHERE sold_at >= NOW() - CAST(:interval AS INTERVAL)
        """,
        {"interval": f"{days} days"},
    )
    if not rows or rows[0][0] is None:
        return {"itens_ativos": 0, "total_vendas": 0, "volume_total": "0"}
    r = rows[0]
    return {
        "itens_ativos":  int(r[0] or 0),
        "total_vendas":  int(r[1] or 0),
        "volume_total":  _fmt_gil(r[2]),
    }


# ---------------------------------------------------------------------------
# Consultas de retainers
# ---------------------------------------------------------------------------

def get_my_active_listings() -> pd.DataFrame:
    """Todas as listagens ativas dos meus retainers."""
    rows = _query(
        """
        SELECT
            mr.name                                  AS retainer,
            i.name                                   AS item,
            ic.name                                  AS category,
            CASE WHEN mal.is_hq THEN 'HQ ★' ELSE 'NQ' END AS qualidade,
            mal.price_per_unit                       AS preco,
            mal.quantity                             AS qtd,
            mal.price_per_unit * mal.quantity        AS total,
            mal.first_seen_at                        AS desde
        FROM my_active_listings mal
        JOIN my_retainers     mr  ON mr.id       = mal.retainer_id
        JOIN items            i   ON i.item_id   = mal.item_id
        JOIN item_subcategory isc ON isc.id       = i.subcategory_id
        JOIN item_category    ic  ON ic.id        = isc.category_id
        ORDER BY mal.last_seen_at DESC
        """
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "retainer", "item", "category", "qualidade", "preco", "qtd", "total", "desde"
    ])
    df["preco_fmt"] = df["preco"].apply(_fmt_gil)
    df["total_fmt"] = df["total"].apply(_fmt_gil)
    df["desde"]     = pd.to_datetime(df["desde"]).dt.strftime("%d/%m %H:%M")
    return df


def get_my_sales(days: int = 30) -> pd.DataFrame:
    """Histórico de vendas inferidas dos meus retainers."""
    rows = _query(
        """
        SELECT
            mr.name                                      AS retainer,
            i.name                                       AS item,
            ic.name                                      AS category,
            CASE WHEN mis.is_hq THEN 'HQ ★' ELSE 'NQ' END AS qualidade,
            mis.price_per_unit                           AS preco,
            mis.quantity                                 AS qtd,
            mis.price_per_unit * mis.quantity            AS total_gil,
            mis.estimated_sold_at                        AS vendido_em
        FROM my_inferred_sales mis
        JOIN my_retainers     mr  ON mr.id       = mis.retainer_id
        JOIN items            i   ON i.item_id   = mis.item_id
        JOIN item_subcategory isc ON isc.id       = i.subcategory_id
        JOIN item_category    ic  ON ic.id        = isc.category_id
        WHERE mis.estimated_sold_at >= NOW() - CAST(:interval AS INTERVAL)
        ORDER BY mis.estimated_sold_at DESC
        """,
        {"interval": f"{days} days"},
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "retainer", "item", "category", "qualidade",
        "preco", "qtd", "total_gil", "vendido_em",
    ])
    df["preco_fmt"]    = df["preco"].apply(_fmt_gil)
    df["total_fmt"]    = df["total_gil"].apply(_fmt_gil)
    df["vendido_em"]   = pd.to_datetime(df["vendido_em"]).dt.strftime("%d/%m %H:%M")
    return df


def get_retainer_kpis(days: int = 30) -> dict:
    """KPIs dos meus retainers para o período especificado."""
    rows_sales = _query(
        """
        SELECT
            COUNT(*)                                       AS vendas,
            COALESCE(SUM(price_per_unit * quantity), 0)    AS total_gil
        FROM my_inferred_sales
        WHERE estimated_sold_at >= NOW() - CAST(:interval AS INTERVAL)
        """,
        {"interval": f"{days} days"},
    )
    rows_active = _query("SELECT COUNT(*) FROM my_active_listings")
    s = rows_sales[0] if rows_sales else (0, 0)
    return {
        "vendas":      int(s[0] or 0),
        "total_gil":   _fmt_gil(s[1]),
        "listagens":   int(rows_active[0][0] if rows_active else 0),
    }
