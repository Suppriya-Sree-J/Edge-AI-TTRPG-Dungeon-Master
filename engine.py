import json
import os
import re
import sqlite3
import sqlite_vec
import struct
import random
import urllib.request
from fastembed import TextEmbedding

INTENT_MATRIX = {
    # 1. Combat & Math (Deterministic 5e Rules)
    "trigger_combat_math": [
        "attack", "strike", "hit", "shoot", "cast", "swing", "stab", "punch",
        "initiative", "fight", "kill", "destroy", "charge", "slash",
        "snipe", "target", "battle", "engage", "roll for initiative", "spell", "cantrip"
    ],

    # 2. Horde & Swarm Mechanics (Damage Pooling)
    "trigger_horde_rules": [
        "horde", "swarm", "mobs", "army", "pack", "dozens", "crowd",
        "hundreds", "group of", "mob of", "cluster", "mass", "legion",
        "multitude", "squad", "troop", "gang", "infestation"
    ],

    # 4. Monument & Mystery (Interactive Set Pieces)
    "trigger_monument_discovery": [
        "statue", "pillar", "altar", "obelisk", "shrine", "carving",
        "mosaic", "mural", "fresco", "runes", "monument", "effigy",
        "inscription", "monolith", "hieroglyphs", "relic", "gargoyle"
    ],

    # 5. Social & NPCs (Generation and Interaction)
    "trigger_npc_interaction": [
        "talk to", "ask", "greet", "bartender", "guard", "merchant",
        "who is", "approach", "speak with", "bribe", "persuade",
        "intimidate", "question", "interrogate", "converse",
        "shopkeeper", "villager", "innkeeper"
    ],

    # 6. Loot & Discovery (Rewards)
    "trigger_loot_tables": [
        "loot", "chest", "pouch", "gold", "coins", "treasure", "reward",
        "magic item", "potion", "scroll", "steal",
        "search the body", "check his pockets", "spoils", "vault", "lockbox"
    ],

    # 7. Scene Setting (The "Strong Start")
    "trigger_scene_setting": [
        "enter the", "arrive at", "travel to", "wake up", "go to the",
        "open the door", "step into", "approach the town", "reach the",
        "journey to", "look at the room", "what do i see", "walk into",
        "look around", "scan the room"
    ],

    # 8. Uncharted Path (Procedural Exploration)
    "trigger_uncharted_path": [
        "go down the hall", "where does this lead", "explore the woods",
        "open the next door", "head north", "follow the path", "walk down",
        "take the stairs", "continue through", "navigate", "push forward",
        "cross the bridge", "descend into", "enter the cave"
    ],

    # 9. Mechanics & Skill Checks (General d20 Tests mapped to the 18 Skills)
    "trigger_mechanics_check": [
        # Strength: Athletics
        "athletics", "jump", "climb", "swim", "grapple", "lift", "break",
        "smash", "hoist", "shove", "wrestle", "force open",

        # Dexterity: Acrobatics, Sleight of Hand, Stealth
        "acrobatics", "flip", "tumble", "balance", "dive", "roll", "stunt",
        "sleight of hand", "pickpocket", "palm", "conceal", "swipe", "plant",
        "stealth", "sneak", "hide", "move quietly", "shadow", "slip past", "tail",

        # Intelligence: Arcana, History, Investigation, Nature, Religion
        "arcana", "magical origin", "magic lore",
        "history", "recall lore", "ancient times", "historical", "remember when",
        "investigation", "deduce", "figure out", "examine", "piece together", "look closely",
        "nature", "flora", "fauna", "weather", "terrain", "plants",
        "religion", "gods", "holy symbol", "cult", "pantheon", "divine", "ritual",

        # Wisdom: Animal Handling, Insight, Medicine, Perception, Survival
        "animal handling", "pet the", "tame", "calm the", "mount the", "soothe",
        "insight", "sense motive", "read him", "read her", "body language", "lying", "telling the truth",
        "medicine", "heal", "stabilize", "diagnose", "autopsy", "first aid", "cause of death",
        "perception", "spot", "listen", "notice", "hear", "smell", "keep watch",
        "survival", "forage", "track", "footprints", "hunt", "trail",

        # Charisma: Deception, Intimidation, Performance, Persuasion
        "deception", "lie", "bluff", "disguise", "trick", "con", "mislead",
        "intimidation", "threaten", "scare", "frighten", "stare down", "awe",
        "performance", "sing", "dance", "act", "play music", "entertain", "distract",
        "persuasion", "convince", "beg", "negotiate", "haggle", "coax", "charm", "plead",

        # Direct Roll Callouts
        "make a check", "roll a d20", "skill check", "ability check"
    ],

    # 10. The Campfire (Downtime & Pacing)
    "trigger_campfire_downtime": [
        "take a rest", "make camp", "short rest", "sleep", "long rest",
        "set up camp", "pitch a tent", "downtime", "heal up",
        "take a breather", "sit by the fire", "rest for the night",
        "recover", "take watch", "end the day"
    ],

    # 11. Historical Ledger Scan (Memory Retrieval)
    "trigger_ledger_memory": [
        "what was", "who was", "where did", "when did", "why did",
        "how did", "remember", "recall", "did we", "have we", "remind me",
        "what is the name", "who is the"
    ],

    # 12. Ending the Session (Wraps up the story instead of running forever)
    "trigger_end_session": [
        "end the adventure", "end the session", "end the game", "that's it for today",
        "let's end here", "let's stop here", "wrap it up", "that concludes our session",
        "we're done for today", "let's call it there", "end our adventure",
        "finish the story", "conclude the adventure"
    ]
}

# --- Dice / damage resolution helpers (deterministic — never left to the LLM) ---

DAMAGE_DICE_PATTERN = re.compile(r'\b(\d*d\d+(?:\s*[+-]\s*\d+)?)\b', re.IGNORECASE)


def roll_dice(expression: str) -> int:
    """Rolls a dice expression like '1d6+1', '2d8-2', or a flat modifier like '3'."""
    expression = (expression or "").strip().replace(" ", "")
    match = re.match(r'^(\d*)d(\d+)([+-]\d+)?$', expression, re.IGNORECASE)
    if not match:
        try:
            return int(expression)
        except ValueError:
            return 0
    num_dice = int(match.group(1)) if match.group(1) else 1
    die_size = int(match.group(2))
    modifier = int(match.group(3)) if match.group(3) else 0
    return sum(random.randint(1, die_size) for _ in range(num_dice)) + modifier


# --- Opening lore/intro. Generated fresh by the LLM each session for variety,
# seeded with random flavor words so a 1B model doesn't produce the same
# setting every time. Edit the word lists to fit your actual game's tone.
LORE_SEED_LOCATIONS = ["a frost-locked mountain pass", "a sunken coastal ruin",
                        "a border town on the edge of a cursed forest",
                        "an abandoned mining colony", "a floating sky-citadel"]
LORE_SEED_THREATS = ["a failing ancient ward", "a returning plague of shadow creatures",
                      "a tyrant's army on the march", "a forgotten god stirring awake",
                      "a magical blight spreading through the land"]
LORE_SEED_HOOKS = ["a bounty posted by desperate town elders",
                    "a mysterious letter calling them to act",
                    "the only survivors of a doomed expedition",
                    "hired mercenaries with nothing left to lose"]


class EdgeDMEngine:
    def __init__(self, db_path="vault.db", ledger_path="ledger.json", characters_path="characters.json", max_memory=5):
        self.db_path = db_path
        self.ledger_path = ledger_path
        self.characters_path = characters_path

        # Helper words for the memory scanner
        self.STOP_WORDS = {"what", "who", "where", "when", "why", "how", "is", "the", "a", "an", "do", "did"}

        # Embed Model loaded here
        self.embed_model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

        # --- THE SMART BRAIN (Vector Database for Rules) ---
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)

        # --- THE MEMORY & STATE ---
        self.ledger = self._load_json(self.ledger_path)
        self.characters = self._load_json(self.characters_path)

        if "full_transcript" not in self.ledger:
            self.ledger["full_transcript"] = []
        if "session" not in self.ledger:
            self.ledger["session"] = {"turn_index": 0}
        if "turn_order" not in self.ledger:
            self.ledger["turn_order"] = []

        self.memory_buffer = []
        self.max_memory = max_memory

        self.compiled_matrix = {
            intent: re.compile(r'\b(?:' + '|'.join(triggers) + r')\b', re.IGNORECASE)
            for intent, triggers in INTENT_MATRIX.items()
        }

    def _load_json(self, filepath):
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else {}
        except Exception:
            return {}

    def save_state(self):
        with open(self.ledger_path, "w", encoding="utf-8") as f:
            json.dump(self.ledger, f, indent=4)
        with open(self.characters_path, "w", encoding="utf-8") as f:
            json.dump(self.characters, f, indent=4)

    def log_player_speech(self, player_name: str, text: str):
        log_entry = f"{player_name}: {text}"
        self.ledger["full_transcript"].append(log_entry)
        self.save_state()
        self._update_short_term_memory(log_entry)

    def log_ai_speech(self, text: str):
        log_entry = f"DM: {text}"
        self.ledger["full_transcript"].append(log_entry)
        self.save_state()
        self._update_short_term_memory(log_entry)

    def log_ai_response(self, text: str):
        self.log_ai_speech(text)

    def _update_short_term_memory(self, log_entry: str):
        self.memory_buffer.append(log_entry)
        if len(self.memory_buffer) > self.max_memory:
            self.memory_buffer.pop(0)

    def get_ai_context(self) -> str:
        return "\n".join(self.memory_buffer)

    def update_full_character_sheet(self, player_id: str, sheet_data: dict):
        self.characters[player_id] = sheet_data
        self.save_state()

    def update_character_stat(self, player_id: str, stat_name: str, new_value) -> bool:
        if player_id in self.characters:
            self.characters[player_id][stat_name] = new_value
            self.save_state()
            return True
        return False

    def update_ledger_event(self, event_key: str, event_data):
        self.ledger[event_key] = event_data
        self.save_state()

    def detect_player_intent(self, action_text: str) -> tuple[str, str]:
        """Returns a tuple: (intent_name, specific_trigger_word)"""
        for intent_name, pattern in self.compiled_matrix.items():
            match = pattern.search(action_text)
            if match:
                return intent_name, match.group(0).lower()
        return "base_story", ""

    def _get_embedding(self, text: str):
        """Talks to FastEmbed to turn player speech into vectors."""
        return list(self.embed_model.embed([text]))[0]

    def get_vault_context(self, action_text: str) -> str:
        """Finds the closest rule by measuring vector distance."""
        query_vector = self._get_embedding(action_text)
        query_bytes = struct.pack(f"{len(query_vector)}f", *query_vector)

        try:
            cursor = self.db.cursor()
            cursor.execute("""
                SELECT vault_text.rule_name, vault_text.rule_data
                FROM vault_embeddings
                INNER JOIN vault_text ON vault_embeddings.rowid = vault_text.rowid
                WHERE vault_embeddings.embedding MATCH ?
                ORDER BY vault_embeddings.distance
                LIMIT 2
            """, [query_bytes])

            results = cursor.fetchall()
            if not results:
                return ""

            vault_matches = [f"{row[0].upper()}: {row[1]}" for row in results]
            return "\n[RULEBOOK CONTEXT]\n" + "\n".join(vault_matches) + "\n"
        except sqlite3.OperationalError:
            return ""

    def search_ledger_history(self, action_text: str) -> str:
        """Searches past transcript entries for relevant keywords."""
        words = [w.lower() for w in re.findall(r'\w+', action_text) if w.lower() not in self.STOP_WORDS]
        matches = []
        for line in self.ledger.get("full_transcript", []):
            if any(w in line.lower() for w in words):
                matches.append(line)
        if matches:
            return "\n[MEMORY SEARCH]\n" + "\n".join(matches[-5:]) + "\n"
        return ""

    def resolve_attack_damage(self, target_id: str, rulebook_context: str,
                               fallback_dice: str = "1d6", explicit_dice: str | None = None) -> dict:
        """
        Deterministically resolves damage in Python instead of asking the LLM to
        compute it. Priority for which dice to roll:
          1. explicit_dice — e.g. the caster's actual spell dice from their sheet
          2. a dice expression found in rulebook_context (vault search result)
          3. fallback_dice
        Rolls it, applies it to the target's HP in self.characters, and returns a
        fact-sheet the LLM can narrate but must not recompute.
        """
        if explicit_dice:
            dice_expr = explicit_dice
        else:
            dice_match = DAMAGE_DICE_PATTERN.search(rulebook_context or "")
            dice_expr = dice_match.group(1).replace(" ", "") if dice_match else fallback_dice

        damage = roll_dice(dice_expr)

        target = self.characters.get(target_id, {})
        # Figure out which HP field this sheet actually uses.
        if "hpCurrent" in target:
            hp_field = "hpCurrent"
        elif "hp" in target:
            hp_field = "hp"
        elif "hit_points" in target:
            hp_field = "hit_points"
        else:
            hp_field = "hp"  # new sheet with no HP field yet — default to "hp"

        current_hp = target.get(hp_field, 0)
        new_hp = max(0, current_hp - damage)

        if target_id in self.characters:
            self.update_character_stat(target_id, hp_field, new_hp)

        return {
            "dice_used": dice_expr,
            "damage": damage,
            "target_id": target_id,
            "previous_hp": current_hp,
            "new_hp": new_hp,
            "defeated": new_hp <= 0,
            "target_found": target_id in self.characters,
        }

    def find_spell_damage_dice(self, player_id: str, action_text: str) -> str | None:
        """
        Checks the caster's own character sheet for a prepared spell whose name
        appears in the action text, and returns its damage dice (e.g. '1d8') if
        found. This is far more reliable than searching the rulebook vault, since
        the exact spell data already lives on the character sheet.
        """
        caster = self.characters.get(player_id, {})
        for spell in caster.get("preparedSpells", []):
            spell_name = spell.get("name", "")
            if spell_name and spell_name.lower() in action_text.lower():
                damage_str = spell.get("damage", "")
                dice_match = DAMAGE_DICE_PATTERN.search(damage_str)
                if dice_match:
                    return dice_match.group(1).replace(" ", "")
        return None

    def get_active_players(self) -> list[dict]:
        """
        Returns character sheets that look like real player characters (they
        have a 'class' field, unlike monster sheets which only have
        name/hp/source). Used to personalize the opening narration around
        whoever's actually been entered via the website.
        """
        return [c for c in self.characters.values() if "class" in c]

    def generate_opening_narration(self, model: str = "llama3.2:1b") -> str:
        """
        Generates a fresh world/lore intro via the LLM, seeded with random
        flavor words so a 1B model doesn't repeat the same setting every time.
        This is a real LLM call (~seconds to under a minute on this hardware) —
        call it once per new session, not per turn. Caches the result so a
        server restart / re-fetch doesn't need to regenerate unless you ask
        for a new one.
        """
        location = random.choice(LORE_SEED_LOCATIONS)
        threat = random.choice(LORE_SEED_THREATS)
        hook = random.choice(LORE_SEED_HOOKS)

        players = self.get_active_players()
        if players:
            party_desc = ", ".join(
                f"{p.get('name', 'Adventurer')} ({p.get('race', '')} {p.get('class', '')})".strip()
                for p in players
            )
        else:
            party_desc = "a group of adventurers"

        intro_prompt = f"""[SYSTEM]
You are the Dungeon Master opening a new fantasy tabletop RPG session for this party:
{party_desc}

Write a short, vivid opening narration (4-6 sentences) that sets the scene. Include:
- A hint of the wider world and its current trouble: {threat}
- Where the party currently is: {location}
- How they got involved: {hook}
- Address the party by their character names naturally in the narration.
End by describing what the party sees and senses right now, in the present moment,
so they know where to begin. Do not ask any questions. Do not break character.

DM:"""

        narration = self.generate_narration(intro_prompt, model=model, max_tokens=220)
        self.ledger["current_intro"] = narration
        self.save_state()
        self.log_ai_speech(narration)
        return narration

    def get_opening_narration(self, model: str = "llama3.2:1b") -> str:
        """
        Generates and returns the opening narration for THIS session, using
        whichever character sheets have been entered so far — call this only
        once your team has actually uploaded character sheets and is ready to
        begin (e.g. a 'Begin Adventure' button on your website). This is a
        real LLM call (~20-40s on this hardware), which is fine here since it
        happens once at the actual start of the game, not per turn.
        """
        narration = self.generate_opening_narration(model=model)
        self.ledger["session_started"] = True
        self.save_state()
        return narration

    def get_session_status(self) -> dict:
        """Lets the frontend poll whether the story has begun yet, and who's
        been entered as a player so far — drives a 'waiting for players...'
        screen before the team hits Begin Adventure."""
        players = self.get_active_players()
        return {
            "session_started": bool(self.ledger.get("session_started", False)),
            "player_count": len(players),
            "player_names": [p.get("name", "Adventurer") for p in players],
        }

    def get_turn_order(self) -> list[dict]:
        """Returns participating player characters sorted by their playOrder
        field (set on the website's 'Select Players & Order' tab). Only
        includes characters marked participating=True; falls back to
        including everyone with a 'class' field if nobody's been explicitly
        marked participating yet, so this still works even if that step was
        skipped."""
        players = self.get_active_players()
        participating = [p for p in players if p.get("participating") is True]
        pool = participating if participating else players
        return sorted(pool, key=lambda p: p.get("playOrder", 0))

    def get_current_turn_player_id(self) -> str | None:
        """Returns the player_id whose turn it currently is, based on
        current_turn_index in the ledger and the playOrder-sorted roster.
        Returns None if there are no participating players yet."""
        order = self.get_turn_order()
        if not order:
            return None
        index = self.ledger.get("current_turn_index", 0) % len(order)
        return order[index].get("id") or self._find_id_for_character(order[index])

    def _find_id_for_character(self, sheet: dict) -> str | None:
        """Character sheets don't always store their own key, so look it up
        from self.characters if 'id' isn't present on the sheet itself."""
        for cid, data in self.characters.items():
            if data is sheet:
                return cid
        return None

    def advance_turn(self):
        """Moves to the next player in turn order. Called automatically after
        every processed action, so voice_loop.py never needs to track whose
        turn it is itself — it just asks /api/current_turn each time."""
        order = self.get_turn_order()
        if not order:
            return
        current = self.ledger.get("current_turn_index", 0)
        self.ledger["current_turn_index"] = (current + 1) % len(order)
        self.save_state()

    def process_player_action(self, player_id: str, action_text: str) -> tuple[str, str, dict | None, bool]:
        """Wrapper method to process player speech, log it, and produce the master prompt."""
        self.log_player_speech(player_id or "Player", action_text)
        intent_name, trigger_word = self.detect_player_intent(action_text)
        result = self.build_master_prompt(player_id, action_text, intent_name, trigger_word)
        self.advance_turn()
        return result

    def build_master_prompt(self, player_id: str, action_text: str, intent_name: str, trigger_word: str) -> tuple[str, str, dict | None, bool]:
        """The Core Brain. Stitches together sheets, rules, memory, and picks the LoRA."""
        recent_history = self.get_ai_context()
        active_sheet = self.characters.get(player_id, {})
        sheet_context = f"\n[ACTIVE CHARACTER SHEET: {active_sheet.get('name', 'Unknown')}]\n" + json.dumps(active_sheet, indent=2) if active_sheet else ""

        dynamic_context = ""
        system_instruction = "You are the Dungeon Master. Respond to the player's action, advance the story, and be descriptive."
        active_lora = "/models/loras/base.safetensors"
        combat_result = None  # surfaced to the API response so the frontend can show exact numbers
        session_ended = False  # True only when trigger_end_session fires

        # Combat/horde use a slimmer context (name + HP only) instead of the full
        # sheet dump, since the LLM doesn't need full stats to narrate a hit —
        # this cuts prompt size and speeds up generation.
        slim_sheet_context = (
            f"\n[CHARACTER: {active_sheet.get('name', 'Unknown')}, "
            f"HP: {active_sheet.get('hpCurrent', active_sheet.get('hp', '?'))}]\n"
            if active_sheet else ""
        )

        # 1. Combat & Math — damage is rolled and applied HERE in Python.
        # The LLM only narrates the already-computed outcome; it never does the math.
        if intent_name == "trigger_combat_math":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/combat.safetensors"
            sheet_context = slim_sheet_context

            # Prefer the caster's own spell data (exact) over the rulebook vault
            # search (approximate) for the damage dice.
            spell_dice = self.find_spell_damage_dice(player_id, action_text)

            # target_id comes from wherever your CV/board-scan (or manual testing)
            # sets it via /api/update_ledger with event_key "current_enemy_id".
            target_id = self.ledger.get("current_enemy_id", "current_enemy")
            result = self.resolve_attack_damage(
                target_id, dynamic_context,
                fallback_dice=spell_dice or "1d6"
            )
            combat_result = result

            if not result["target_found"]:
                system_instruction = (
                    f"The player attempted an attack, but no valid target named "
                    f"'{target_id}' exists yet. Ask the player or DM to clarify who "
                    "or what they're attacking, without describing any damage or death."
                )
            else:
                system_instruction = (
                    f"We are in combat. The player's attack deals {result['damage']} damage "
                    f"(rolled {result['dice_used']}). State the damage number and the "
                    f"target's remaining HP ({result['new_hp']}) explicitly and clearly "
                    f"at the end of your narration so the player knows the exact numbers"
                    f"{', and note the target is defeated' if result['defeated'] else ''}. "
                    "Narrate this outcome vividly using the [RULEBOOK CONTEXT] for flavor. "
                    "Do NOT invent additional attacks, extra damage, or events the player "
                    "did not take. Do not recompute the damage number — use exactly what is given."
                )

        # 2. Horde & Swarm Mechanics — same deterministic approach, mob-scale fallback dice.
        elif intent_name == "trigger_horde_rules":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/combat.safetensors"
            sheet_context = slim_sheet_context

            target_id = self.ledger.get("current_enemy_id", "current_horde")
            result = self.resolve_attack_damage(target_id, dynamic_context, fallback_dice="2d6")
            combat_result = result

            if not result["target_found"]:
                system_instruction = (
                    f"The player attempted an attack on a horde/swarm, but no valid "
                    f"target named '{target_id}' exists yet. Ask the player or DM to "
                    "clarify what they're attacking, without describing any damage or destruction."
                )
            else:
                system_instruction = (
                    f"The player is facing a swarm/horde. The attack deals {result['damage']} "
                    f"damage total to the mob (rolled {result['dice_used']}), bringing it from "
                    f"{result['previous_hp']} to {result['new_hp']} HP"
                    f"{' and it is destroyed' if result['defeated'] else ''}. "
                    "Describe the overwhelming numbers and this outcome. Do not add extra "
                    "combatants or recompute the damage — use exactly what is given."
                )

        # 3. Hazards & Environment
        elif intent_name == "trigger_trap_generation":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/hazards.safetensors"
            system_instruction = "A trap, puzzle, or environmental hazard is involved. Use the rulebooks to resolve the mechanism and its effects."

        # 4. Monument & Mystery
        elif intent_name == "trigger_monument_discovery":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/worldbuilding.safetensors"
            system_instruction = "The player has come across something, provide a monument and mystery. Provide deep lore and visual descriptions."

        # 5. Social & NPCs
        elif intent_name == "trigger_npc_interaction":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/social.safetensors"
            system_instruction = "The player is interacting with an NPC. Roleplay the conversation naturally and determine the NPC's reaction."

        # 6. Loot & Discovery
        elif intent_name == "trigger_loot_tables":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/loot.safetensors"
            system_instruction = "The player is searching for loot or rewards. Determine exactly what they find based on the rulebooks."

        # 7. Scene Setting
        elif intent_name == "trigger_scene_setting":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/worldbuilding.safetensors"
            system_instruction = "The player is entering a new area. Provide a rich sensory description of the environment to set the scene."

        # 8. Uncharted Path
        elif intent_name == "trigger_uncharted_path":
            if random.random() < 0.20:
                dynamic_context = self.get_vault_context("trap mechanism hidden pitfall poison wire")
                active_lora = "/models/loras/hazards.safetensors"
                system_instruction = "SURPRISE TRAP! The player was exploring and walked right into a hidden hazard. Use the [RULEBOOK CONTEXT] to describe the trap springing, and demand a saving throw."
            else:
                dynamic_context = self.get_vault_context(action_text)
                active_lora = "/models/loras/exploration.safetensors"
                system_instruction = "The player is exploring forward into the unknown. Generate what they encounter next procedurally."

        # 9. Mechanics & Skill Checks
        elif intent_name == "trigger_mechanics_check":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/mechanics.safetensors"
            system_instruction = f"The player is attempting a specific action involving '{trigger_word}'. Determine the relevant skill check according to specific skill and its outcome."

        # 10. The Campfire (Downtime)
        elif intent_name == "trigger_campfire_downtime":
            dynamic_context = self.get_vault_context(action_text)
            active_lora = "/models/loras/base.safetensors"
            system_instruction = "The party is taking a rest or making camp. Describe the quiet moment and let them heal or prepare."

        # 11. Historical Ledger Scan (Memory)
        elif intent_name == "trigger_ledger_memory":
            dynamic_context = self.search_ledger_history(action_text)
            active_lora = "/models/loras/base.safetensors"
            system_instruction = "The player is trying to recall something. Look ONLY at the [MEMORY SEARCH] context and remind them of the factual history."

        # 12. Ending the Session — wraps up the story instead of running forever.
        elif intent_name == "trigger_end_session":
            active_lora = "/models/loras/base.safetensors"
            session_ended = True
            self.ledger["session_ended"] = True
            self.save_state()
            system_instruction = (
                "The party has decided to end the adventure here for now. Write a "
                "satisfying concluding narration (3-5 sentences) that wraps up the "
                "current scene using the [RECENT HISTORY] below, reflects on what "
                "the party has accomplished so far, and gives a sense of closure "
                "or a hook for next time. Do not ask any questions. Do not "
                "continue the action — this is the ending."
            )

        # Catch-All for anything else
        elif intent_name != "base_story":
            dynamic_context = self.get_vault_context(action_text)
            system_instruction = f"The player triggered a {intent_name} event. Consult the [RULEBOOK CONTEXT] to resolve it accurately."

        final_prompt = f"""[SYSTEM]
Keep your response brief and always end on a complete sentence — never trail off mid-thought.
{system_instruction}
{sheet_context}
{dynamic_context}
[RECENT HISTORY]
{recent_history}
{player_id}: {action_text}

DM:"""
        return final_prompt, active_lora, combat_result, session_ended

    def generate_narration(self, prompt: str, model: str = "llama3.2:1b", max_tokens: int = 110) -> str:
        """Sends the generated master prompt directly to the local Ollama LLM.
        max_tokens caps generation length — keep this short (e.g. 40-70) for
        quick combat/action turns, and higher (e.g. 200) for longer narration
        like the opening intro, so it doesn't cut off mid-sentence."""
        url = "http://127.0.0.1:11434/api/generate"
        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "num_predict": max_tokens,
                "num_ctx": 1024
            }
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                response_text = result.get("response", "").strip()
                print(f"[DM NARRATION]: {response_text}")
                return response_text
        except Exception as e:
            return f"[LLM Offline or Error: {e}]"