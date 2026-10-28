reset_db:
    docker compose exec app python scripts/reset_db.py

pytest:
    docker compose exec app pytest -v