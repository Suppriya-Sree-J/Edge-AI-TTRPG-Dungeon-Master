"""
Voice loop for Edge DM. Runs entirely on the UNO Q — no laptop involved.

Push-to-talk: the current player holds a physical button wired to the STM32
core while they speak, and releases it when done. Recording starts and stops
with the button, so the party can discuss freely without the DM listening in.
Transcribes offline (vosk), sends it to the game API, speaks the reply offline
(piper). No internet used anywhere in this loop.

SETUP — fill these in once your hardware is plugged in:
1. Run `arecord -l` and put your mic's card/device numbers in MIC_DEVICE below.
2. Run `aplay -l` and find your sound card's numbers.
3. Flash sketch.ino to the MCU so the button events reach this script.
4. Character turn order comes automatically from playOrder set on the
   website's 'Select Players & Order' tab — no player ID to set here.
"""

import json
import os
import signal
import subprocess
import threading
import time
import wave

import requests
import vosk

# ---------------------------------------------------------------- SETTINGS
API_URL = "http://127.0.0.1:5000/api/player_action"
CURRENT_TURN_URL = "http://127.0.0.1:5000/api/current_turn"

MIC_DEVICE = "plughw:2,0"         # confirmed working: webcam's built-in mic
# (the 3.5mm mic via sound card, plughw:1,0, gave static — not used)
VOSK_MODEL_PATH = "vosk-model-small-en-us-0.15"
PIPER_MODEL = "en_US-amy-medium.onnx"
WAV_PATH = "/tmp/action.wav"

# --------------------------------------------------------- BUTTON SETTINGS
# "event" — the MCU pushes button_event to us (default, lowest latency)
# "poll"  — we ask the MCU for button_state every POLL_INTERVAL seconds
#           Switch to this if the event path ever stops delivering.
BUTTON_MODE = "event"

POLL_INTERVAL = 0.03        # 30 ms — imperceptible, cheap
MIN_HOLD_SECONDS = 0.35     # ignore accidental taps
MAX_RECORD_SECONDS = 30     # safety cap if the button sticks
FALLBACK_RECORD_SECONDS = 8 # used only if the Bridge is unavailable


# ------------------------------------------------------------------ BRIDGE
_button = {"pressed": False}
_bridge_ready = False

try:
    from arduino.app_utils import App, Bridge
    _bridge_ready = True
except ImportError:
    print("WARNING: arduino.app_utils not found.")
    print("         Falling back to a fixed recording window — button disabled.")


def _on_button_event(pressed):
    """Called by the MCU on every debounced edge."""
    _button["pressed"] = bool(pressed)


def start_bridge() -> bool:
    """Connects to the Arduino router. Returns True if the button is live."""
    if not _bridge_ready:
        return False

    if BUTTON_MODE == "event":
        Bridge.provide("button_event", _on_button_event)
        # App.run() is blocking and owns whichever thread it's on, so it goes
        # on a daemon thread and the turn loop keeps the main thread.
        threading.Thread(target=App.run, daemon=True, name="arduino-bridge").start()
        time.sleep(1.0)   # let the router handshake finish before turn one

    # Sanity check: can we actually reach the MCU?
    try:
        state = Bridge.call("button_state")
        print(f"Bridge connected. Button currently {'DOWN' if state else 'UP'}.")
        return True
    except Exception as exc:
        print(f"WARNING: Bridge reachable but button_state failed: {exc}")
        print("         Is sketch.ino flashed to the MCU?")
        return BUTTON_MODE == "event"   # events may still arrive


def button_is_pressed() -> bool:
    if not _bridge_ready:
        return False
    if BUTTON_MODE == "poll":
        try:
            return bool(Bridge.call("button_state"))
        except Exception:
            return False
    return _button["pressed"]


def wait_for_button(target: bool, timeout=None) -> bool:
    """Blocks until the button reaches `target` state. True if it got there,
    False if it timed out."""
    deadline = None if timeout is None else time.time() + timeout
    while True:
        if button_is_pressed() is target:
            return True
        if deadline is not None and time.time() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


# ------------------------------------------------------------------ AUDIO
def record_action() -> bool:
    """Records while the button is held. Returns True if we captured
    something usable, False if the turn should be skipped."""

    if not _bridge_ready:
        print(f"\nRecording for {FALLBACK_RECORD_SECONDS}s — speak now...")
        subprocess.run([
            "arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
            "-c", "1", "-d", str(FALLBACK_RECORD_SECONDS), WAV_PATH
        ], check=True)
        return True

    print("\nHold the button and speak...")
    wait_for_button(True)

    print("Recording — release when done.")
    started = time.time()

    # No -d flag: arecord runs until we stop it.
    proc = subprocess.Popen([
        "arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
        "-c", "1", WAV_PATH
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    released = wait_for_button(False, timeout=MAX_RECORD_SECONDS)

    # SIGINT, not SIGKILL — arecord traps it and finalises the WAV header.
    # Killing it outright leaves a header claiming zero frames and vosk
    # silently transcribes nothing.
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    held = time.time() - started

    if not released:
        print(f"Hit the {MAX_RECORD_SECONDS}s cap — is the button stuck?")
    if held < MIN_HOLD_SECONDS:
        print(f"Only held {held:.2f}s — ignoring as an accidental tap.")
        return False
    if not os.path.exists(WAV_PATH) or os.path.getsize(WAV_PATH) < 2000:
        print("No usable audio captured.")
        return False

    print(f"Captured {held:.1f}s.")
    return True


def transcribe(model) -> str:
    """Runs offline speech-to-text on the recorded wav file."""
    wf = wave.open(WAV_PATH, "rb")
    rec = vosk.KaldiRecognizer(model, wf.getframerate())
    rec.SetWords(True)

    text_parts = []
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            text_parts.append(json.loads(rec.Result()).get("text", ""))
    text_parts.append(json.loads(rec.FinalResult()).get("text", ""))
    wf.close()

    return " ".join(t for t in text_parts if t).strip()


def speak(text: str):
    """Offline text-to-speech via Piper, routed to the USB sound card."""
    if not text:
        return
    subprocess.run(
        f'echo "{text}" | ~/.local/bin/piper --model {PIPER_MODEL} '
        f'--output_file - 2>/dev/null | aplay -D plughw:1,0',
        shell=True
    )


def get_current_turn():
    """Asks the backend whose turn it currently is. Returns (player_id, name)
    or (None, None) if no participating players exist yet."""
    try:
        resp = requests.get(CURRENT_TURN_URL, timeout=10)
        if resp.status_code != 200:
            return None, None
        data = resp.json()
        return data.get("player_id"), data.get("name")
    except Exception as e:
        print(f"Could not fetch current turn: {e}")
        return None, None


# ------------------------------------------------------------------- MAIN
def main():
    if not os.path.isdir(VOSK_MODEL_PATH):
        print(f"ERROR: vosk model folder '{VOSK_MODEL_PATH}' not found.")
        print("Download it with:")
        print("  wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip")
        print("  unzip vosk-model-small-en-us-0.15.zip")
        return

    print("Connecting to the MCU...")
    start_bridge()

    print("Loading speech recognition model...")
    model = vosk.Model(VOSK_MODEL_PATH)
    print("Ready.")

    speak("The dungeon master is ready.")

    while True:
        player_id, player_name = get_current_turn()
        if player_id is None:
            speak("No adventurers are set up yet.")
            print("No participating players found. Add characters and mark them "
                  "participating on the website.")
            time.sleep(5)
            continue

        speak(f"{player_name}, what do you do?")

        try:
            captured = record_action()
        except subprocess.CalledProcessError as e:
            print(f"Recording failed: {e}")
            print("Check MIC_DEVICE matches your `arecord -l` output.")
            continue

        if not captured:
            continue

        action_text = transcribe(model)

        if not action_text:
            print("Didn't catch that — press the button and try again.")
            continue

        print(f"{player_name} said: {action_text}")

        try:
            resp = requests.post(
                API_URL,
                json={"player_id": player_id, "action_text": action_text},
                timeout=120  # narration can take up to ~40s on this hardware
            )
            data = resp.json()
        except Exception as e:
            print(f"Backend error: {e}")
            speak("I could not reach the dungeon master engine.")
            continue

        ai_text = data.get("ai_response", "")
        print(f"DM: {ai_text}")
        speak(ai_text)

        combat_result = data.get("combat_result")
        if combat_result and combat_result.get("target_found"):
            print(f"  [{combat_result['dice_used']} → {combat_result['damage']} damage · "
                  f"{combat_result['previous_hp']} → {combat_result['new_hp']} HP"
                  f"{' · defeated' if combat_result['defeated'] else ''}]")

        if data.get("session_ended"):
            print("\nSession ended.")
            break


if __name__ == "__main__":
    main()
