#!/bin/bash
# setup_audio.sh — installs everything voice_loop.py needs.
# Safe to run before hardware is plugged in — none of this needs the mic/speaker.
set -e

echo "=== Installing system packages ==="
sudo apt-get update
sudo apt-get install -y libportaudio2 unzip

echo "=== Installing Python packages ==="
pip install vosk requests piper-tts --break-system-packages

echo "=== Downloading Piper voice model ==="
cd ~/dm_engine
if [ ! -f "en_US-amy-medium.onnx" ]; then
    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
fi

echo "=== Downloading offline speech recognition model ==="
cd ~/dm_engine
if [ ! -d "vosk-model-small-en-us-0.15" ]; then
    wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip vosk-model-small-en-us-0.15.zip
else
    echo "Model already present, skipping download."
fi

echo ""
echo "=== Sanity checks ==="
python3 -c "import vosk; print('vosk: OK')"
espeak-ng "Setup complete" && echo "espeak-ng: OK (you should have heard that, if a speaker is connected)"

echo ""
echo "=== Done. Next steps: ==="
echo "1. Plug in hardware, then run:  lsusb && arecord -l && aplay -l"
echo "2. Fill in MIC_DEVICE and PLAYER_ID in voice_loop.py"
echo "3. Run:  chmod +x start.sh && ./start.sh"
