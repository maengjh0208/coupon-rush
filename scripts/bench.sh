#!/usr/bin/env bash
set -euo pipefail

RATES=${RATES:-"100 300 500 700 1000"}
RUNS=${RUNS:-3}
COMPOSE="docker compose -f docker-compose.yaml -f docker-compose.bench.yaml"
OUT="bench-results/$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"

mkdir -p "$OUT"
$COMPOSE up -d

for rate in $RATES; do
  for run in $(seq 1 "$RUNS"); do
    file="$OUT/rate${rate}-run${run}.txt"
    echo "RATE=$rate run=$run"
    $COMPOSE exec -T app python scripts/reset_db.py 100000
    k6 run --quiet -e RATE="$rate" k6/issue.js > "$file" 2>&1
    $COMPOSE exec -T postgres psql -U coupon_rush -c \
      "SELECT (SELECT count(*) FROM coupon_issue) AS issue_rows, issued_quantity FROM coupon WHERE id = 1;" >> "$file"
    sleep 5
  done
done

echo "결과: $OUT"