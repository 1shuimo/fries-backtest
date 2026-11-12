#!/usr/bin/env bash
# Run the whole daily monitor outside GitHub Actions (used from the Claude session while
# Actions jobs cannot start): download prices, fill a lagging day, build the report,
# email it through Resend. Prints the report path, or why nothing was sent.
# Env: MAIL_TO (required); RESEND_API_KEY defaults to a placeholder the session proxy replaces.
# Usage: tools/run_local_monitor.sh [YYYY-MM-DD]   (default: last closed New York session)
set -euo pipefail
cd "$(dirname "$0")/.."
: "${MAIL_TO:?set MAIL_TO to the address that receives the report}"
export RESEND_API_KEY="${RESEND_API_KEY:-proxy-injected}"
tmp=$(mktemp -d)

python3 tools/monitor_schedule.py --asof "${1:-}" --output "$tmp/day.txt"
day=$(sed -n 's/^date=//p' "$tmp/day.txt")
if [ -z "${1:-}" ] && grep -q '^skip=true' "$tmp/day.txt"; then
  echo "RESULT: skipped, outside the delivery window for $day"; exit 0
fi
out="strategies/quant/monitor/reports/$day.md"

cp strategies/friestrader/pool.txt "$tmp/pool.txt"
tail -n +2 strategies/quant/monitor/positions.csv | cut -d, -f1 >> "$tmp/pool.txt"
state=stale
for attempt in 1 2 3; do
  python3 tools/fetch_yahoo.py --pool "$tmp/pool.txt" --start 2025-01-01 > "$tmp/fetch.log" 2>&1 || true
  python3 tools/fill_latest_bar.py --date "$day" --pool "$tmp/pool.txt" > "$tmp/fill.log" 2>&1 || true
  grep "fallback for" "$tmp/fill.log" || true
  set +e
  python3 strategies/quant/monitor/daily_monitor.py --require-date "$day" --check-only
  code=$?
  set -e
  if [ $code -eq 0 ]; then state=ready; break; fi
  if [ $code -eq 3 ]; then echo "RESULT: $day is not a NYSE trading day, nothing sent"; exit 0; fi
  [ $attempt -lt 3 ] && { echo "data for $day incomplete (attempt $attempt), waiting 5 min"; sleep 300; }
done
if [ "$state" != ready ]; then
  echo "盯盘 $day：行情还没更新到 $day，今天没有生成报告，请不要按旧报告操作" > "$tmp/summary.txt"
  printf '# 盯盘 %s：行情未更新\n\n重试 3 次后股票池覆盖仍不足 90%%，没有生成报告。\n' "$day" > "$tmp/stale.md"
  python3 tools/notify.py "$tmp/stale.md" "$tmp/summary.txt" || true
  echo "RESULT: stale data for $day, warning emailed"; exit 4
fi

python3 tools/fetch_names.py --limit 40 > /dev/null 2>&1 || true
python3 strategies/quant/monitor/daily_monitor.py --asof "$day" --require-date "$day" --out "$out" \
  --summary-out "$tmp/summary.txt" --html-out "$tmp/email.html" --text-out "$tmp/email.txt" \
  --link "https://github.com/1shuimo/fries-backtest/blob/main/$out" > /dev/null
# EMAIL_FORMAT=text sends only the plain-text version (the HTML version has not been reaching Gmail).
html_arg=(--html "$tmp/email.html")
[ "${EMAIL_FORMAT:-html}" = text ] && html_arg=()
python3 tools/notify.py "$out" "$tmp/summary.txt" "https://github.com/1shuimo/fries-backtest/blob/main/$out" \
  "${html_arg[@]}" --text "$tmp/email.txt"
echo "SUMMARY: $(cat "$tmp/summary.txt")"
echo "RESULT: report $out emailed"
