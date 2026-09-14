"""
dm_cues.py — Linux (MPU) side of the reflex layer.

One import for engine.py. Wraps three things:

    sfx("combat_start")     buzzer jingle   (reuses dm_sound.py)
    light_on() / light_off() RGB strip      (on during combat only)
    take_physical_roll()    the d20 the player actually threw, or None

Every call is fire-and-forget and swallows its own errors. If the MCU is
unplugged or the camera is busy, the game carries on exactly as it did
before any of this hardware existed.

Start it once, from app.py:

    import dm_cues
    dm_cues.start(tray_roi=(120, 80, 400, 400))
"""

import threading
import time

# --- sound: reuse the module already on the board -------------------------
try:
    from dm_sound import sfx, set_enabled            # noqa: F401
    _SOUND = True
except Exception as exc:                              # pragma: no cover
    print(f"[cues] dm_sound unavailable ({exc}) — sound disabled")
    _SOUND = False

    def sfx(name: str) -> None:
        pass

    def set_enabled(on: bool) -> None:
        pass

# --- bridge ---------------------------------------------------------------
try:
    from arduino.app_utils import App, Bridge
    _BRIDGE = True
except ImportError:
    print("[cues] arduino.app_utils not found — light and tray disabled")
    _BRIDGE = False

# --- dice reader ----------------------------------------------------------
try:
    from dice_reader import DiceReader
    _DICE = True
except Exception as exc:
    print(f"[cues] dice_reader unavailable ({exc}) — physical dice disabled")
    _DICE = False


# =========================================================== COMBAT LIGHT
_light_state = {"on": False}


def light_on() -> None:
    """Strip ON. Called when combat begins."""
    _set_light(True)


def light_off() -> None:
    """Strip OFF. Called when combat ends."""
    _set_light(False)


def light_is_on() -> bool:
    return _light_state["on"]


def _set_light(on: bool) -> None:
    if _light_state["on"] == on:
        return                      # don't spam the bridge with no-ops
    _light_state["on"] = on
    if not _BRIDGE:
        return

    def _send():
        try:
            Bridge.call("dm_light", on)
        except Exception as exc:
            print(f"[cues] light {'on' if on else 'off'} failed: {exc}")

    threading.Thread(target=_send, daemon=True).start()


# ============================================================ PHYSICAL DICE
ROLL_TTL_SECONDS = 30.0     # an unclaimed roll expires back to software
READ_TIMEOUT = 6.0          # how long the camera waits for the die to settle

_pending = {"value": None, "at": 0.0}
_reader = None
_reader_lock = threading.Lock()
_tray_roi = None


def _on_dice_landed() -> None:
    """Notification from the MCU: the tray piezo felt the die hit.
    Wakes the camera and reads the face inside the tray box."""
    if not _DICE:
        return
    threading.Thread(target=_read_die, daemon=True).start()


def _read_die() -> None:
    global _reader
    # Serialise: two knocks in quick succession must not open the camera twice.
    if not _reader_lock.acquire(blocking=False):
        return
    try:
        if _reader is None:
            _reader = DiceReader(tray_roi=_tray_roi)

        # require_motion=False: the piezo already told us it moved, and by the
        # time we get here the die may have stopped. Waiting for motion we've
        # already missed would just time out.
        result = _reader.wait_for_roll(timeout=READ_TIMEOUT, require_motion=False)

        if result is None:
            print("[dice] couldn't find the die in the tray")
            sfx("error")
            return
        if not result.confident:
            print(f"[dice] low confidence: read {result.value} "
                  f"(score {result.score:.2f}) — asking for a re-roll")
            sfx("error")
            return

        _pending["value"] = result.value
        _pending["at"] = time.time()
        print(f"[dice] read {result.value} (score {result.score:.2f})")

        if result.value == 20:
            sfx("crit")
        elif result.value == 1:
            sfx("fumble")
    except Exception as exc:
        print(f"[dice] read failed: {exc}")
    finally:
        _reader_lock.release()


def take_physical_roll():
    """Returns the face value the player actually threw, or None.

    Consumes it — a single throw can only be used once, so two attacks in a
    row can't silently reuse the same number.
    """
    value = _pending["value"]
    if value is None:
        return None
    if time.time() - _pending["at"] > ROLL_TTL_SECONDS:
        _pending["value"] = None
        return None
    _pending["value"] = None
    return value


def has_physical_roll() -> bool:
    return (_pending["value"] is not None
            and time.time() - _pending["at"] <= ROLL_TTL_SECONDS)


# ==================================================================== START
_started = False


def start(tray_roi=None) -> None:
    """Call once at server startup. tray_roi is (x, y, w, h) in pixels —
    the box the die is thrown into, in camera coordinates."""
    global _started, _tray_roi
    if _started:
        return
    _started = True
    _tray_roi = tray_roi

    if not _BRIDGE:
        return

    Bridge.provide("dice_landed", _on_dice_landed)
    threading.Thread(target=App.run, daemon=True, name="arduino-bridge").start()
    time.sleep(1.0)

    light_off()          # known state at boot
    print("[cues] reflex layer connected")
