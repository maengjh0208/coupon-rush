.PHONY: reset-db pytest

reset-db:
	docker compose exec app python scripts/reset_db.py $(QTY)

pytest:
	docker compose exec app pytest -v