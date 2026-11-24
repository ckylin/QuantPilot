"""Database maintenance CLI.

Usage:
    python -m app.db.cli init-db          # create tables
    python -m app.db.cli init-db --seed   # create tables + seed base assets
"""

from __future__ import annotations

import argparse

from app.core.config import settings
from app.db.session import configure_database, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="QuantPilot database tools")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init-db", help="create missing tables")
    p.add_argument("--seed", action="store_true", help="seed base data assets")
    args = parser.parse_args()

    if not settings.database_url:
        raise SystemExit("请先设置 QP_DATABASE_URL（见 quantpilot-server/.env.example）")
    configure_database()
    init_db()
    print("数据库表已就绪")

    if args.seed:
        from app.data.seed import seed_catalog
        from app.db.factories import build_data_service
        service = build_data_service()
        seed_catalog(service)
        print("种子数据已写入（幂等）")


if __name__ == "__main__":
    main()
