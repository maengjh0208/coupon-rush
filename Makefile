.PHONY: reset-db pytest bench formatter linter linter-fix

reset-db:
	docker compose exec app python scripts/reset_db.py $(QTY)

pytest:
	docker compose exec app pytest -v $(ARGS)

# make bench RATES=2000
bench:
	./scripts/bench.sh

formatter:
	docker compose exec app ruff format .

# make linter
linter:
	docker compose exec app ruff check .
linter-fix:
	docker compose exec app ruff check . --fix

