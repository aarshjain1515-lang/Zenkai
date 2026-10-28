from alembic import context
from sqlalchemy import create_engine, pool

from zenkai.config import settings
from zenkai.db import Base, database_url

config = settings()
url = config.database_url_unpooled or config.database_url
if not url:
    raise RuntimeError("Set DATABASE_URL_UNPOOLED to the direct Neon connection string before migrating")
engine = create_engine(database_url(url), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
