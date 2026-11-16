.PHONY: reset-db pytest bench fmt

reset-db:
	docker compose exec app python scripts/reset_db.py $(QTY)

pytest:
	docker compose exec app pytest -v $(ARGS)

bench:
	./scripts/bench.sh RATES=1500

fmt:
	docker compose exec app ruff format .

