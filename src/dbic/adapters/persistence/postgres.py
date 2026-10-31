import logging

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from dbic.settings import Settings

SCHEMA_REVISION = "0002_m1"


def create_database(settings: Settings) -> Engine:
    return create_engine(
        settings.postgres_url(),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=3,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
    )


def database_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            return revision == SCHEMA_REVISION
    except SQLAlchemyError as exc:
        # Connection exceptions may contain credentials; expose only dependency state.
        logging.getLogger("dbic").warning("PostgreSQL readiness failed type=%s", type(exc).__name__)
        return False
