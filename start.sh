#!/bin/bash
# start.sh — brings up the whole system with one command.
# Called automatically at boot by the dm-engine systemd service (see setup
# instructions), or run manually over SSH any time.

# Give USB devices (mic, camera, sound card via the hub) time to enumerate
# after power-on before anything tries to use them.
sleep 15

cd ~/dm_engine

echo "Starting Flask backend..."
python3 app.py > flask.log 2>&1 &
FLASK_PID=$!

echo "Waiting for the model to warm up..."
until grep -q "LLM warm and ready" flask.log 2>/dev/null; do
    sleep 1
done

echo "Backend ready. Starting voice loop..."
python3 voice_loop.py

# If voice_loop.py exits (session ended), Flask keeps running in the background.
# Kill it manually with: kill $FLASK_PID   (or `pkill -f app.py`)
