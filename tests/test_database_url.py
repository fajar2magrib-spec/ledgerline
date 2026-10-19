"""A database link copied from Neon / Supabase / Heroku must work without editing it."""

from ledgerline.config import normalize_database_url as norm


def test_hosted_postgres_links_are_rewritten_for_the_bundled_driver():
    assert norm("postgresql://u:p@ep-x.neon.tech/db?sslmode=require") == "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
    assert norm("postgres://u:p@host:5432/db") == "postgresql+psycopg://u:p@host:5432/db"


def test_links_that_already_name_a_driver_are_untouched():
    for url in ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg2://u:p@h/db", "mysql+pymysql://u:p@h/db", "sqlite:///x.db"):
        assert norm(url) == url
