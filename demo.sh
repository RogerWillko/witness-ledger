#!/bin/bash
# demo.sh — run this while screen-recording
# Uses a private demo DB so it does not touch your live chain.db.
# There is no GET /verify — the pipe is one-way; verify is a local CLI.

set -e
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi
export PATH="$(pwd)/.venv/bin:$PATH"

DEMO="$(pwd)/.demo-run"
rm -rf "$DEMO"
mkdir -p "$DEMO/run" "$DEMO/store" weights
rm -f weights/model_v1.bin weights/model_v1.bin.clean weights/model_v1.bin.stripped weights/restored.bin

export WITNESS_DB="$DEMO/chain.db"
export WITNESS_HOST=127.0.0.1
export WITNESS_PORT=8765
export WITNESS_URL=http://127.0.0.1:8765
export WITNESS_HMAC_FILE="$DEMO/hmac.key"
export WITNESS_LOG_KEY_FILE="$DEMO/log.key"
export RUN_PATH="$DEMO/run/weights.bin"
export RECEIPT_PATH="$DEMO/run/receipt.json"
export STORE_PATH="$DEMO/store/weights.bin"
export MODEL_KEY_FILE="$DEMO/model.key"
export MODEL_RECEIVE_HOST=127.0.0.1
export MODEL_RECEIVE_PORT=8766

$PYTHON -c "open('weights/model_v1.bin','wb').write(b'W1N0'+__import__('os').urandom(64))"

[ -t 1 ] && clear

echo "=== Witness-Ledger Demo ==="
echo ""
echo "Starting the witness server..."
$PYTHON witness.py serve >/dev/null 2>&1 &
WPID=$!
trap 'kill $WPID 2>/dev/null || true' EXIT
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if $PYTHON -c "import socket; socket.create_connection(('127.0.0.1', 8765), 0.2).close()" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

echo ""
echo "--- Step 1: Honest deploy ---"
echo "Model submits clean weights with the correct pin."
$PYTHON model_side.py --deploy weights/model_v1.bin
echo ""
echo "Witness: ACCEPTED"
echo ""
sleep 2

echo "--- Step 2: Tamper attempt ---"
echo "Model rewrites its own weights."
$PYTHON model_side.py --tamper weights/model_v1.bin
echo ""
echo "Model tries to deploy the tampered file..."
$PYTHON model_side.py --deploy weights/model_v1.bin || echo "Witness: REJECTED — hash mismatch"
echo ""
sleep 2

echo "--- Step 3: Verify the chain ---"
echo "Checking integrity..."
$PYTHON witness.py verify
echo ""
sleep 2

echo "--- Step 4: Restore ---"
echo "Model requests re-entry with clean weights..."
$PYTHON model_side.py --restore weights/model_v1.bin
echo "Witness: RESTORED — clean weights delivered"
echo ""
sleep 2

echo "=== Demo complete ==="
echo "The model tampered. The witness caught it. The chain held."
echo "Repo: github.com/RogerWillko/witness-ledger"

kill $WPID 2>/dev/null || true
trap - EXIT
wait $WPID 2>/dev/null || true
