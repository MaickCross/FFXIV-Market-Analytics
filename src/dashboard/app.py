import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback_context, dash_table, dcc, html

from src.config import settings
from src.dashboard.queries import (
    get_categories,
    get_last_collection,
    get_market_kpis,
    get_my_active_listings,
    get_my_sales,
    get_price_history,
    get_retainer_kpis,
    get_top_items,
)

# ---------------------------------------------------------------------------
# Instância do app
# ---------------------------------------------------------------------------

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    title="FFXIV Market Analytics",
    suppress_callback_exceptions=True,
)

# ---------------------------------------------------------------------------
# Paleta de cores (DARKLY)
# ---------------------------------------------------------------------------
COLORS = {
    "bg":       "#222222",
    "surface":  "#2a2a2a",
    "border":   "#3a3a3a",
    "text":     "#e0e0e0",
    "muted":    "#888888",
    "primary":  "#375a7f",
    "selected": "#4a7ba8",
    "nq":       "#4e9af1",   # azul para NQ
    "hq":       "#f0c040",   # dourado para HQ
    "green":    "#00bc8c",
    "row_even": "#222222",
    "row_odd":  "#272727",
}

# ---------------------------------------------------------------------------
# Estilos de DataTable reutilizáveis
# ---------------------------------------------------------------------------
TABLE_STYLE = dict(
    style_header={
        "backgroundColor": COLORS["surface"],
        "color": COLORS["text"],
        "fontWeight": "600",
        "border": f"1px solid {COLORS['border']}",
        "fontSize": "13px",
    },
    style_data={
        "backgroundColor": COLORS["bg"],
        "color": COLORS["text"],
        "border": f"1px solid {COLORS['border']}",
        "fontSize": "13px",
    },
    style_data_conditional=[
        {"if": {"row_index": "odd"}, "backgroundColor": COLORS["row_odd"]},
        {
            "if": {"state": "selected"},
            "backgroundColor": COLORS["selected"],
            "border": f"1px solid {COLORS['primary']}",
            "color": "#ffffff",
        },
    ],
    style_cell={"padding": "6px 12px", "textAlign": "left", "whiteSpace": "normal"},
    style_as_list_view=True,
)


# ---------------------------------------------------------------------------
# Componentes auxiliares
# ---------------------------------------------------------------------------

def kpi_card(title: str, value: str, subtitle: str = "", color: str = COLORS["primary"]) -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([
            html.P(title, className="text-muted mb-1", style={"fontSize": "12px"}),
            html.H4(value, className="mb-0", style={"color": color, "fontWeight": "700"}),
            html.Small(subtitle, className="text-muted") if subtitle else html.Span(),
        ]),
        className="h-100",
        style={"backgroundColor": COLORS["surface"], "border": f"1px solid {COLORS['border']}"},
    )


def empty_chart(msg: str = "Selecione um item na tabela para ver a tendência de preço") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg, showarrow=False,
        font=dict(color=COLORS["muted"], size=14),
        xref="paper", yref="paper", x=0.5, y=0.5,
    )
    fig.update_layout(**_chart_layout(""))
    return fig


def _chart_layout(title: str) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=COLORS["text"], size=15)),
        paper_bgcolor=COLORS["surface"],
        plot_bgcolor=COLORS["bg"],
        font=dict(color=COLORS["text"]),
        xaxis=dict(gridcolor=COLORS["border"], tickfont=dict(color=COLORS["muted"]), showgrid=True),
        yaxis=dict(gridcolor=COLORS["border"], tickfont=dict(color=COLORS["muted"]), showgrid=True),
        legend=dict(font=dict(color=COLORS["text"]), bgcolor=COLORS["surface"]),
        margin=dict(l=60, r=20, t=50, b=40),
        hovermode="x unified",
    )


# ---------------------------------------------------------------------------
# Layout — Aba Mercado
# ---------------------------------------------------------------------------

mercado_layout = html.Div([
    # Filtros
    dbc.Row([
        dbc.Col([
            html.Label("Categoria", className="text-muted mb-1", style={"fontSize": "12px"}),
            dcc.Dropdown(
                id="filter-category",
                options=[{"label": "Todas", "value": "__all__"}],
                value="__all__",
                clearable=False,
                style={"backgroundColor": COLORS["surface"], "color": COLORS["text"]},
                className="dash-dropdown-dark",
            ),
        ], md=4),
        dbc.Col([
            html.Label("Período", className="text-muted mb-1", style={"fontSize": "12px"}),
            dbc.RadioItems(
                id="filter-period",
                options=[
                    {"label": " 7 dias",  "value": 7},
                    {"label": " 14 dias", "value": 14},
                    {"label": " 30 dias", "value": 30},
                ],
                value=7,
                inline=True,
                className="mt-1",
            ),
        ], md=4),
    ], className="mb-3 mt-3"),

    # KPIs
    dbc.Row(id="market-kpis", className="mb-3 g-3"),

    # Tabela de top itens
    html.H6("Top Itens por Rotatividade", className="text-muted mb-2"),
    dash_table.DataTable(
        id="market-table",
        columns=[],
        data=[],
        sort_action="native",
        row_selectable="single",
        selected_rows=[],
        page_size=20,
        page_action="native",
        **TABLE_STYLE,
        style_table={"overflowX": "auto"},
    ),

    # Gráfico
    html.Div([
        html.Hr(style={"borderColor": COLORS["border"], "marginTop": "24px"}),
        html.H6(id="chart-title", children="Selecione um item para ver a tendência de preço",
                className="text-muted mb-2"),
        dcc.Graph(id="price-chart", figure=empty_chart(), config={"displayModeBar": False}),
    ]),
], className="px-1")


# ---------------------------------------------------------------------------
# Layout — Aba de Retainers
# ---------------------------------------------------------------------------

retainers_layout = html.Div([
    # KPIs
    dbc.Row(id="retainer-kpis", className="mb-3 mt-3 g-3"),

    # Listagens ativas
    html.H6("Listagens Ativas", className="text-muted mb-2"),
    dash_table.DataTable(
        id="active-listings-table",
        columns=[], data=[],
        sort_action="native",
        page_size=15,
        **TABLE_STYLE,
        style_table={"overflowX": "auto"},
    ),

    # Vendas recentes
    html.Hr(style={"borderColor": COLORS["border"], "marginTop": "24px"}),
    html.H6("Vendas Recentes (30 dias)", className="text-muted mb-2"),
    dash_table.DataTable(
        id="sales-table",
        columns=[], data=[],
        sort_action="native",
        page_size=20,
        page_action="native",
        **TABLE_STYLE,
        style_table={"overflowX": "auto"},
    ),
], className="px-1")


# ---------------------------------------------------------------------------
# Layout principal
# ---------------------------------------------------------------------------

app.layout = dbc.Container([
    # Header
    dbc.Row([
        dbc.Col(
            html.H4("FFXIV Market Analytics",
                    style={"color": COLORS["text"], "fontWeight": "700", "marginBottom": "0"}),
            width="auto",
        ),
        dbc.Col(
            html.Div([
                dbc.Badge(settings.WORLD, color="primary", className="me-2"),
                html.Small(id="last-collection", className="text-muted"),
            ], className="d-flex align-items-center"),
            className="d-flex align-items-center",
        ),
    ], className="py-3 mb-1 border-bottom", style={"borderColor": COLORS["border"] + " !important"}),

    # Tabs
    dbc.Tabs([
        dbc.Tab(mercado_layout,   label="📊 Mercado",       tab_id="tab-market"),
        dbc.Tab(retainers_layout, label="🏪 Meus Retainers", tab_id="tab-retainers"),
    ], id="tabs", active_tab="tab-market", className="mb-2"),

    # Auto-refresh a cada 1 hora
    dcc.Interval(id="interval", interval=60 * 60 * 1000, n_intervals=0),

], fluid=True, style={"backgroundColor": COLORS["bg"], "minHeight": "100vh"},
   className="px-4 pb-5")


# ---------------------------------------------------------------------------
# Callbacks — Header
# ---------------------------------------------------------------------------

@app.callback(
    Output("last-collection", "children"),
    Input("interval", "n_intervals"),
)
def update_header(_):
    return f"Última coleta: {get_last_collection()}"


# ---------------------------------------------------------------------------
# Callbacks — Aba Mercado
# ---------------------------------------------------------------------------

@app.callback(
    Output("filter-category", "options"),
    Input("interval", "n_intervals"),
)
def load_categories(_):
    cats = get_categories()
    return [{"label": "Todas", "value": "__all__"}] + [
        {"label": c, "value": c} for c in cats
    ]


@app.callback(
    Output("market-kpis", "children"),
    Output("market-table", "data"),
    Output("market-table", "columns"),
    Input("interval", "n_intervals"),
    Input("filter-period", "value"),
    Input("filter-category", "value"),
)
def update_market(_, days, category):
    days = days or 7
    cat = None if category == "__all__" else category

    kpis = get_market_kpis(days)
    kpi_row = [
        dbc.Col(kpi_card("Itens com vendas",   str(kpis["itens_ativos"]),  f"nos últimos {days}d"), md=4),
        dbc.Col(kpi_card("Total de vendas",    str(kpis["total_vendas"]),  f"nos últimos {days}d"), md=4),
        dbc.Col(kpi_card("Volume financeiro",  kpis["volume_total"] + " gil", f"nos últimos {days}d",
                         color=COLORS["green"]), md=4),
    ]

    df = get_top_items(days=days, category=cat)
    if df.empty:
        return kpi_row, [], []

    display_cols = [
        ("item_name",        "Item"),
        ("category",         "Categoria"),
        ("subcategory",      "Subcategoria"),
        ("gil_per_day_fmt",  "Gil/dia"),
        ("sales_per_day_fmt","Vendas/dia"),
        ("avg_price_fmt",    "Preço médio"),
        ("total_units_fmt",  "Qtd vendida"),
    ]
    columns = [{"name": label, "id": col_id} for col_id, label in display_cols]
    # item_id oculto mas presente nos dados para o callback do gráfico
    columns.append({"name": "item_id", "id": "item_id", "hidden": True})

    records = df[
        ["item_name", "category", "subcategory",
         "gil_per_day_fmt", "sales_per_day_fmt", "avg_price_fmt",
         "total_units_fmt", "item_id"]
    ].to_dict("records")

    return kpi_row, records, columns


@app.callback(
    Output("price-chart", "figure"),
    Output("chart-title", "children"),
    Input("market-table", "selected_rows"),
    State("market-table", "data"),
    State("filter-period", "value"),
)
def update_price_chart(selected_rows, data, days):
    if not selected_rows or not data:
        return empty_chart(), "Selecione um item para ver a tendência de preço"

    row      = data[selected_rows[0]]
    item_id  = row.get("item_id")
    name     = row.get("item_name", "Item")
    days     = days or 7

    df = get_price_history(item_id=int(item_id), days=days)
    if df.empty:
        return empty_chart(f"{name} — sem snapshots no período"), f"Tendência: {name}"

    fig = go.Figure()

    # NQ
    if df["min_price_nq"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["collected_at"], y=df["min_price_nq"],
            name="Mín NQ", mode="lines",
            line=dict(color=COLORS["nq"], dash="dot", width=1.5),
            hovertemplate="%{y:,.0f} gil<extra>Mín NQ</extra>",
        ))
    if df["avg_price_nq"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["collected_at"], y=df["avg_price_nq"],
            name="Média NQ", mode="lines",
            line=dict(color=COLORS["nq"], width=2),
            fill="tonexty", fillcolor="rgba(78,154,241,0.08)",
            hovertemplate="%{y:,.0f} gil<extra>Média NQ</extra>",
        ))

    # HQ (só se tiver dados)
    if df["min_price_hq"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["collected_at"], y=df["min_price_hq"],
            name="Mín HQ", mode="lines",
            line=dict(color=COLORS["hq"], dash="dot", width=1.5),
            hovertemplate="%{y:,.0f} gil<extra>Mín HQ</extra>",
        ))
    if df["avg_price_hq"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["collected_at"], y=df["avg_price_hq"],
            name="Média HQ", mode="lines",
            line=dict(color=COLORS["hq"], width=2),
            fill="tonexty", fillcolor="rgba(240,192,64,0.08)",
            hovertemplate="%{y:,.0f} gil<extra>Média HQ</extra>",
        ))

    fig.update_layout(**_chart_layout(f"Behemoth · últimos {days}d"))
    fig.update_yaxes(tickformat=",.0f", title_text="Gil")
    fig.update_xaxes(title_text="")
    return fig, f"Tendência de preço: {name}"


# ---------------------------------------------------------------------------
# Callbacks — Aba Retainers
# ---------------------------------------------------------------------------

@app.callback(
    Output("retainer-kpis", "children"),
    Output("active-listings-table", "data"),
    Output("active-listings-table", "columns"),
    Output("sales-table", "data"),
    Output("sales-table", "columns"),
    Input("interval", "n_intervals"),
    Input("tabs", "active_tab"),
)
def update_retainers(_, active_tab):
    if active_tab != "tab-retainers":
        return [], [], [], [], []

    kpis = get_retainer_kpis(days=30)
    kpi_row = [
        dbc.Col(kpi_card("Total ganho (30d)",    kpis["total_gil"] + " gil",
                         color=COLORS["green"]), md=4),
        dbc.Col(kpi_card("Vendas realizadas",    str(kpis["vendas"]),     "últimos 30d"), md=4),
        dbc.Col(kpi_card("Listagens ativas",     str(kpis["listagens"]),  "agora"), md=4),
    ]

    # Listagens ativas
    df_active = get_my_active_listings()
    if df_active.empty:
        active_data, active_cols = [], []
    else:
        active_display = [
            ("retainer",   "Retainer"),
            ("item",       "Item"),
            ("category",   "Categoria"),
            ("qualidade",  "Qual."),
            ("preco_fmt",  "Preço"),
            ("qtd",        "Qtd"),
            ("total_fmt",  "Total"),
            ("desde",      "Desde"),
        ]
        active_cols = [{"name": lbl, "id": cid} for cid, lbl in active_display]
        active_data = df_active[[c for c, _ in active_display]].to_dict("records")

    # Vendas recentes
    df_sales = get_my_sales(days=30)
    if df_sales.empty:
        sales_data, sales_cols = [], []
    else:
        sales_display = [
            ("retainer",    "Retainer"),
            ("item",        "Item"),
            ("category",    "Categoria"),
            ("qualidade",   "Qual."),
            ("preco_fmt",   "Preço"),
            ("qtd",         "Qtd"),
            ("total_fmt",   "Total"),
            ("vendido_em",  "Vendido em"),
        ]
        sales_cols = [{"name": lbl, "id": cid} for cid, lbl in sales_display]
        sales_data = df_sales[[c for c, _ in sales_display]].to_dict("records")

    return kpi_row, active_data, active_cols, sales_data, sales_cols


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    print(f"Dashboard disponível em: http://localhost:8050")
    app.run(debug=False, host="0.0.0.0", port=8050)