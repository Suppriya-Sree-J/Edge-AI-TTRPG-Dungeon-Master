Voice loop for Edge DM. Runs entirely on the UNO Q — no laptop involved.
Turn-based, no button needed: after the DM speaks, the party gets a think
window, then it prompts and listens for the party's move. Transcribes
offline (vosk), sends it to the game API, speaks the reply offline
(piper). No internet used anywhere in this loop.

SETUP — fill these in once your hardware is plugged in:
1. Run `arecord -l` and put your mic's card/device numbers in MIC_DEVICE below.
2. Run `aplay -l` and find your sound card's numbers.
3. Character turn order comes automatically from playOrder set on the
   website's 'Select Players & Order' tab — no player ID to set here.
"""

import subprocess
import wave
import json
import time
import requests
import vosk
import os

# ---------------------------------------------------------------- SETTINGS
API_URL = "http://127.0.0.1:5000/api/player_action"

MIC_DEVICE = "plughw:2,0"         # confirmed working: webcam's built-in mic
# (the 3.5mm mic via sound card, plughw:1,0, gave static — not used)
CURRENT_TURN_URL = "http://127.0.0.1:5000/api/current_turn"
VOSK_MODEL_PATH = "vosk-model-small-en-us-0.15"  # TODO: match the unzipped folder name

THINK_SECONDS = 15   # time for the party to discuss before it's their turn
RECORD_SECONDS = 8   # how long it listens once prompted — bump this if 8s
                      # keeps cutting people off mid-sentence
WAV_PATH = "/tmp/action.wav"


# ------------------------------------------------------------------ AUDIO
def record_action():
    """Records RECORD_SECONDS of audio from the mic to a wav file."""
    print(f"\n🎤 Recording for {RECORD_SECONDS}s — speak now...")
    subprocess.run([
        "arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000",
        "-c", "1", "-d", str(RECORD_SECONDS), WAV_PATH
    ], check=True)


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

    return " ".join(t for t in text_parts if t).strip()


PIPER_MODEL = "en_US-amy-medium.onnx"  # TODO: confirm this matches the filename on your board

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

    print("Loading speech recognition model...")
    model = vosk.Model(VOSK_MODEL_PATH)
    print("Ready.")

    speak("The dungeon master is ready.")

    while True:
        print(f"\nDiscuss your move — {THINK_SECONDS}s...")
        time.sleep(THINK_SECONDS)

        player_id, player_name = get_current_turn()
        if player_id is None:
            speak("No adventurers are set up yet.")
            print("No participating players found. Add characters and mark them participating on the website.")
            time.sleep(5)
            continue

        speak(f"{player_name}, what do you do?")

        try:
            record_action()
        except subprocess.CalledProcessError as e:
            print(f"Recording failed: {e}")
            print("Check MIC_DEVICE matches your `arecord -l` output.")
            continue

        action_text = transcribe(model)

        if not action_text:
            print("Didn't catch that — looping back to think time.")
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
