.PHONY: reset-db pytest bench ruff

reset-db:
	docker compose exec app python scripts/reset_db.py $(QTY)

pytest:
	docker compose exec app pytest -v $(ARGS)

bench:
	./scripts/bench.sh

ruff:
	docker compose exec app ruff format .

