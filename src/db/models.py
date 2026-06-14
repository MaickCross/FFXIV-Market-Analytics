from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP


class Base(DeclarativeBase):
    pass


# =============================================================================
#  GRUPO 1 — Catálogo de itens (dados estáticos do jogo)
# =============================================================================


#Categoria Principal do ITEM: Weapons, Armor, Items, Housing
class ItemCategory(Base):

    __tablename__ = "item_category"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    subcategories: Mapped[list["ItemSubcategory"]] = relationship(
        back_populates="category"
    )

#Subcategoria do ITEM: Ex -> Weapons;Conjure's Arms, Armor;Head, Items;Seafood, Housing;Furnishing, ...
class ItemSubcategory(Base):
    
    __tablename__ = "item_subcategory"
    __table_args__ = (UniqueConstraint("category_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("item_category.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    category: Mapped["ItemCategory"] = relationship(back_populates="subcategories")
    items: Mapped[list["Item"]] = relationship(back_populates="subcategory")


class Item(Base):
    """
    Catálogo de itens vendáveis no market board.
    item_id é o ID nativo do jogo — usado direto na URL da API.
    """

    __tablename__ = "items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subcategory_id: Mapped[int] = mapped_column(
        ForeignKey("item_subcategory.id", ondelete="RESTRICT"), nullable=False
    )
    can_be_hq: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    icon_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    subcategory: Mapped["ItemSubcategory"] = relationship(back_populates="items")
    snapshots: Mapped[list["MarketSnapshot"]] = relationship(back_populates="item")
    sales: Mapped[list["SaleHistory"]] = relationship(back_populates="item")
    active_listings: Mapped[list["MyActiveListing"]] = relationship(
        back_populates="item"
    )
    inferred_sales: Mapped[list["MyInferredSale"]] = relationship(
        back_populates="item"
    )


# =============================================================================
#  GRUPO 2 — Retainers do jogador
# =============================================================================


class MyRetainer(Base):
    """Retainers que pertencem ao jogador. Cadastro manual."""

    __tablename__ = "my_retainers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_character: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    active_listings: Mapped[list["MyActiveListing"]] = relationship(
        back_populates="retainer"
    )
    inferred_sales: Mapped[list["MyInferredSale"]] = relationship(
        back_populates="retainer"
    )


# =============================================================================
#  GRUPO 3 — Dados de mercado
# =============================================================================


class MarketSnapshot(Base):
    """
    Snapshot agregado do mercado por item, coletado a cada hora.
    Guarda preço mínimo, médio e volume de listagens para NQ e HQ.
    HQ = None quando o item não suporta versão high quality.
    """

    __tablename__ = "market_snapshots"
    __table_args__ = (UniqueConstraint("item_id", "collected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    min_price_nq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_price_hq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    avg_price_nq: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    avg_price_hq: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    listings_count_nq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    listings_count_hq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    item: Mapped["Item"] = relationship(back_populates="snapshots")


class SaleHistory(Base):
    """
    Histórico de vendas concluídas no Behemoth.
    Fonte: /api/v2/history/Behemoth/{item_id}

    A UNIQUE CONSTRAINT em (item_id, sold_at, price_per_unit, quantity, is_hq, buyer_name)
    garante que o ETL pode sempre tentar inserir sem verificar duplicatas —
    o banco rejeita silenciosamente com ON CONFLICT DO NOTHING.
    """

    __tablename__ = "sale_history"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "sold_at",
            "price_per_unit",
            "quantity",
            "is_hq",
            "buyer_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), nullable=False
    )
    sold_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    price_per_unit: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_hq: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    buyer_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    upload_time: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    item: Mapped["Item"] = relationship(back_populates="sales")


# =============================================================================
#  GRUPO 4 — Monitoramento dos retainers do jogador
# =============================================================================


class MyActiveListing(Base):
    """
    Listagens ATIVAS dos retainers do jogador no momento da coleta.

    first_seen_at: quando vimos essa listagem pela primeira vez.
    last_seen_at:  última coleta em que ainda estava ativa.

    Quando last_seen_at para de ser atualizado → listagem sumiu.
    O scheduler detecta isso e gera um registro em MyInferredSale.
    """

    __tablename__ = "my_active_listings"
    __table_args__ = (UniqueConstraint("retainer_id", "item_id", "is_hq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retainer_id: Mapped[int] = mapped_column(
        ForeignKey("my_retainers.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), nullable=False
    )
    price_per_unit: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_hq: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    retainer: Mapped["MyRetainer"] = relationship(back_populates="active_listings")
    item: Mapped["Item"] = relationship(back_populates="active_listings")
    inferred_sale: Mapped[Optional["MyInferredSale"]] = relationship(
        back_populates="listing"
    )


class MyInferredSale(Base):
    """
    Venda inferida: quando uma listagem ativa desaparece entre dois snapshots.
    listing_id mantém rastreabilidade — você sabe qual listagem originou a venda.
    """

    __tablename__ = "my_inferred_sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retainer_id: Mapped[int] = mapped_column(
        ForeignKey("my_retainers.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("my_active_listings.id", ondelete="SET NULL"), nullable=True
    )
    estimated_sold_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    price_per_unit: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_hq: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    retainer: Mapped["MyRetainer"] = relationship(back_populates="inferred_sales")
    item: Mapped["Item"] = relationship(back_populates="inferred_sales")
    listing: Mapped[Optional["MyActiveListing"]] = relationship(
        back_populates="inferred_sale"
    )