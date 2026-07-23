#!/bin/bash
# Kill every Python process serving on 5050-5099
for port in 5050 5051 5052 5053 5054 5055 5099; do
  pid=$(lsof -t -i:$port 2>/dev/null)
  [ -n "$pid" ] && kill $pid 2>/dev/null && echo "Killed $pid on :$port"
done
sleep 1

cd "$(dirname "$0")"
ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2)
export ANTHROPIC_API_KEY

echo ""
echo "Starting Loomtrip on :5099..."
python3 app.py
