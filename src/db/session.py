from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings

# pool_pre_ping=True faz o SQLAlchemy testar a conexão antes de usá-la.
# Isso evita erros silenciosos quando o container do Postgres reinicia.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Generator[Session, None, None]:
    """
    Context manager de sessão para uso nos módulos ETL e scripts.

    Uso:
        with get_session() as db:
            db.execute(...)
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()