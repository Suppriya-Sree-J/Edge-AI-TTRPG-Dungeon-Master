"""
app.py — Flask API layer for Edge-DM.

Deliberately contains no game logic. It exposes the endpoints that
voice_loop.py, aruco_tracker.py and the web app already call, hands the work
to EdgeDMEngine in engine.py, and warms the LLM at startup so the first real
turn doesn't pay the cold-load cost.

Run:  python3 -u app.py
(the -u matters — see the warm-up note below)
"""

import json
import os
import threading
import urllib.request

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from engine import EdgeDMEngine

# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "llama3.2:1b"

app = Flask(__name__, static_folder=BASE_DIR)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

engine = EdgeDMEngine(
    db_path=os.path.join(BASE_DIR, "vault.db"),
    ledger_path=os.path.join(BASE_DIR, "ledger.json"),
    characters_path=os.path.join(BASE_DIR, "characters.json"),
)

# The web app tags every message with a campaignId. We remember the last one
# we saw so outgoing HP updates are addressed to the right campaign.
_last_campaign_id = {"id": None}


# ----------------------------------------------------------- WEB APP -----
@app.route("/")
def index():
    """Serves the character manager so players can reach it at
    http://<board-ip>:5000 from their phones."""
    return send_from_directory(BASE_DIR, "index.html")


# ------------------------------------------------------- 1. SHEETS ------
@app.route("/api/upload_sheet", methods=["POST"])
def upload_sheet():
    data = request.get_json(force=True) or {}
    player_id = data.get("player_id")
    sheet = data.get("sheet") or data.get("payload") or {}
    if not player_id:
        return jsonify({"error": "player_id required"}), 400
    engine.update_full_character_sheet(player_id, sheet)
    engine.save_state()
    return jsonify({"ok": True, "player_id": player_id})


@app.route("/api/update_stat", methods=["POST"])
def update_stat():
    data = request.get_json(force=True) or {}
    ok = engine.update_character_stat(
        data.get("player_id"), data.get("stat_name"), data.get("new_value")
    )
    if ok:
        engine.save_state()
    return jsonify({"ok": ok})


@app.route("/api/update_ledger", methods=["POST"])
def update_ledger():
    data = request.get_json(force=True) or {}
    engine.update_ledger_event(data.get("event_key"), data.get("event_data"))
    engine.save_state()
    return jsonify({"ok": True})


# ------------------------------------------------------ 2. SESSION ------
@app.route("/api/session_status", methods=["GET"])
def session_status():
    return jsonify(engine.get_session_status())


@app.route("/api/start_session", methods=["POST"])
def start_session():
    narration = engine.get_opening_narration(model=MODEL)
    engine.ledger["session_started"] = True
    engine.log_ai_response(narration)
    engine.save_state()
    return jsonify({"ai_response": narration})


@app.route("/api/current_turn", methods=["GET"])
def current_turn():
    """Polled by voice_loop.py before every recording."""
    player_id = engine.get_current_turn_player_id()
    if not player_id:
        return jsonify({"player_id": None, "name": None})
    sheet = engine.characters.get(player_id, {}) or {}
    return jsonify({"player_id": player_id, "name": sheet.get("name", "Adventurer")})


# ------------------------------------------------------- 3. VISION ------
@app.route("/api/update_positions", methods=["POST"])
def update_positions():
    """Receives grid positions from aruco_tracker.py every 1.5 s."""
    data = request.get_json(force=True) or {}
    engine.update_board_positions(data.get("positions", {}))
    return jsonify({"ok": True})


# ----------------------------------------------------- 4. GAMEPLAY ------
@app.route("/api/player_action", methods=["POST"])
def handle_player_action():
    """The main gameplay endpoint. process_player_action() logs the speech,
    classifies the intent, resolves all combat maths in Python and returns a
    prompt; the model is only asked to narrate the outcome."""
    data = request.get_json(force=True) or {}
    player_id = data.get("player_id")
    action_text = data.get("action_text", "")

    if not action_text:
        return jsonify({"error": "action_text required"}), 400

    prompt, active_lora, combat_result, session_ended = engine.process_player_action(
        player_id, action_text
    )

    narration = engine.generate_narration(prompt, model=MODEL, max_tokens=110)
    engine.log_ai_response(narration)
    engine.save_state()

    # Push the new HP back to every phone in the room.
    if combat_result and combat_result.get("target_found"):
        socketio.emit("campaign_data_update", {
            "type": "hp",
            "campaignId": _last_campaign_id["id"],
            "characterId": combat_result.get("target_id"),
            "payload": {"hpCurrent": combat_result.get("new_hp")},
        })

    return jsonify({
        "ai_response": narration,
        "combat_result": combat_result,
        "session_ended": session_ended,
        "active_lora": active_lora,
    })


# ------------------------------------------------------ SOCKET.IO -------
@socketio.on("connect")
def on_connect():
    print("[bridge] web app connected", flush=True)


@socketio.on("character_data_update")
def on_character_data_update(data):
    """Inbound from the web app: sheets, HP edits, death saves, spell slots,
    participation and turn order. Shape: {type, campaignId, characterId,
    payload, ts}"""
    if not isinstance(data, dict):
        return
    if data.get("campaignId"):
        _last_campaign_id["id"] = data["campaignId"]

    char_id = data.get("characterId")
    payload = data.get("payload") or {}
    kind = data.get("type")

    if kind in ("character", "save_character") and char_id:
        engine.update_full_character_sheet(char_id, payload)
    elif kind == "hp" and char_id:
        if "hpCurrent" in payload:
            engine.update_character_stat(char_id, "hpCurrent", payload["hpCurrent"])
        if "hpMax" in payload:
            engine.update_character_stat(char_id, "hpMax", payload["hpMax"])
    elif kind in ("participation", "play_order") and char_id:
        if "participating" in payload:
            engine.update_character_stat(char_id, "participating", payload["participating"])
        if "playOrder" in payload:
            engine.update_character_stat(char_id, "playOrder", payload["playOrder"])
    elif char_id:
        for key, value in payload.items():
            engine.update_character_stat(char_id, key, value)
    else:
        engine.update_ledger_event(kind or "webapp_event", payload)

    engine.save_state()


# -------------------------------------------------------- WARM-UP -------
def warmup_model():
    """Loads the LLM into RAM at startup so the first turn isn't slow.

    start.sh greps flask.log for the exact string below, so do not change it.
    flush=True is essential: stdout is redirected to a file, so Python
    block-buffers it and the line would otherwise sit in the buffer while
    start.sh waits forever."""
    try:
        body = json.dumps({
            "model": MODEL,
            "prompt": "Say ready.",
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_predict": 8, "num_ctx": 1024},
        }).encode()
        req = urllib.request.Request(
            OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=300).read()
        print("LLM warm and ready", flush=True)
    except Exception as exc:
        # Still release start.sh — a broken model is better surfaced by the
        # first turn failing audibly than by the boot hanging in silence.
        print(f"[warmup] failed: {exc}", flush=True)
        print("LLM warm and ready", flush=True)


threading.Thread(target=warmup_model, daemon=True).start()


if __name__ == "__main__":
    # 0.0.0.0, not 127.0.0.1 — players reach the web app from their phones.
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
