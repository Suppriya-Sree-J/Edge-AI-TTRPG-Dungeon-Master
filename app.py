from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from engine import EdgeDMEngine
import threading

app = Flask(__name__)
# Enable CORS so browser requests from the LAN are not blocked
CORS(app)
dm_engine = EdgeDMEngine()


def warmup_model():
    """Loads the LLM into memory at server startup instead of on the first
    real request. Deliberately does NOT pre-generate the opening narration —
    that happens later, personalized to whichever character sheets have
    actually been entered, when the team hits Begin Adventure."""
    print("Warming up LLM (loading into memory)...")
    dm_engine.generate_narration("Say ready.", model="llama3.2:1b")
    print("LLM warm and ready.")


threading.Thread(target=warmup_model, daemon=True).start()


@app.route('/api/upload_sheet', methods=['POST'])
def upload_sheet():
    """Website POSTs the JSON character sheet here."""
    data = request.json or {}
    player_id = data.get('player_id')
    sheet_data = data.get('sheet')
    dm_engine.update_full_character_sheet(player_id, sheet_data)
    return jsonify({"status": "success", "message": "Sheet received"}), 200


@app.route('/api/update_stat', methods=['POST'])
def update_stat():
    """Website sends payload when a character stat changes (HP, spells)."""
    data = request.json or {}
    player_id = data.get('player_id')
    stat_name = data.get('stat')
    new_value = data.get('value')
    success = dm_engine.update_character_stat(player_id, stat_name, new_value)
    if success:
        return jsonify({"status": "success", "message": f"{stat_name} updated"}), 200
    else:
        return jsonify({"status": "error", "message": "Player ID not found"}), 404


@app.route('/api/update_ledger', methods=['POST'])
def update_ledger():
    """Website sends payload when the environment/location changes."""
    data = request.json or {}
    event_key = data.get('event_key')
    event_data = data.get('event_data')
    dm_engine.update_ledger_event(event_key, event_data)
    return jsonify({"status": "success", "message": "Ledger updated"}), 200


@app.route('/api/session_status', methods=['GET'])
def session_status():
    """Frontend polls this to show a 'waiting for players...' screen listing
    who's been entered so far, and to know when the story has begun."""
    return jsonify(dm_engine.get_session_status())


@app.route('/api/start_session', methods=['POST'])
def start_session():
    """Call this when your team is ready to begin — e.g. a 'Begin Adventure'
    button on the website, pressed after character sheets are entered. Generates
    a fresh opening narration personalized to whichever characters exist so far.
    This is a real LLM call (~20-40s on this hardware) — expected here, since
    it's a one-time 'the world is generating...' moment at the true start of
    the game, not a per-turn cost."""
    narration = dm_engine.get_opening_narration()
    return jsonify({
        "status": "success",
        "ai_response": narration
    })


@app.route('/api/current_turn', methods=['GET'])
def current_turn():
    """Returns whose turn it currently is, based on the playOrder set on the
    website's 'Select Players & Order' tab. voice_loop.py calls this before
    each recording instead of using a fixed player_id."""
    player_id = dm_engine.get_current_turn_player_id()
    if player_id is None:
        return jsonify({"status": "error", "message": "No participating players yet"}), 404
    sheet = dm_engine.characters.get(player_id, {})
    return jsonify({
        "status": "success",
        "player_id": player_id,
        "name": sheet.get("name", "Adventurer")
    })


@app.route('/api/player_action', methods=['POST'])
def handle_player_action():
    data = request.get_json() or {}
    player_id = data.get('player_id', 'player_1')
    action_text = data.get('action_text', '')

    # 1. Build context prompt & route LoRA. combat_result is None for non-combat
    #    intents, and a dict with exact numbers (damage, new_hp, defeated, etc.)
    #    for combat/horde intents. session_ended is True only when the player
    #    triggers an end-of-adventure phrase — the frontend should stop taking
    #    further actions once it sees this.
    prompt, active_lora, combat_result, session_ended = dm_engine.process_player_action(player_id, action_text)

    # 2. Generate AI DM response. Endings get more room to write a proper
    #    wrap-up instead of the tight budget used for regular turns.
    ai_response = dm_engine.generate_narration(prompt, max_tokens=200 if session_ended else 110)

    # 3. Return full payload
    return jsonify({
        "status": "success",
        "action_text": action_text,
        "active_lora": active_lora,
        "ai_response": ai_response,
        "llm_prompt": prompt,
        "combat_result": combat_result,
        "session_ended": session_ended
    })


@app.route('/api/log_ai', methods=['POST'])
def log_ai():
    """Website sends the AI's final response here to be logged permanently."""
    data = request.json or {}
    ai_response = data.get('ai_text')
    dm_engine.log_ai_response(ai_response)
    return jsonify({"status": "success", "message": "AI response logged"}), 200


@app.route('/')
def serve_website():
    """Serves the HTML directly from your dm_engine folder"""
    return send_file('dnd-manager(1).html')


if __name__ == '__main__':
    # Binds to 0.0.0.0 so your laptop can see it over Wi-Fi
    app.run(host='0.0.0.0', port=5000)