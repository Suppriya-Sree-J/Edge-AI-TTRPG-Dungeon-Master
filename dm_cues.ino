/*
 * dm_cues.ino — Edge-DM reflex layer, STM32 side of the UNO Q.
 *
 * Merges everything that must feel instant into one non-blocking loop:
 *   - passive piezo buzzer  (D3)  event jingles, priority-ranked
 *   - RGB strip on/off      (D5)  via ULN2803, ON during combat only
 *   - dice tray piezo disc  (A0)  detects the die landing
 *   - push-to-talk button   (D2)  gates the microphone
 *
 * Nothing here uses delay(). Every subsystem steps forward once per loop()
 * pass, so a playing jingle never stops the button or tray from responding.
 *
 * WIRING
 *   buzzer (+)   -> 100R -> D3        buzzer (-)  -> GND
 *   ULN2803 IN1  -> D5                OUT1 -> strip (-),  strip (+) -> 12V
 *   ULN2803 GND  -> board GND AND 12V supply GND   (common ground is required)
 *   piezo disc   -> A0, with 1M resistor A0->GND to bleed the charge off
 *   button leg1  -> D2                button leg2 -> GND   (INPUT_PULLUP)
 *
 * BRIDGE METHODS PROVIDED TO LINUX
 *   dm_sound(int id)      play a jingle by id (see SoundId below)
 *   dm_sound_stop()       cut whatever is playing
 *   dm_light(bool on)     strip on/off
 *   button_state() -> bool current debounced button state
 *
 * BRIDGE NOTIFICATIONS SENT TO LINUX
 *   button_event(bool pressed)
 *   dice_landed()
 */

#include <Arduino_RouterBridge.h>

#define BUZZER_PIN 3
#define LIGHT_PIN  5
#define PIEZO_PIN  A0
#define BUTTON_PIN 2

/* ---------------- note frequencies (Hz) ---------------- */
#define R      0
#define D3n  147
#define G3   196
#define A3   220
#define AS3  233
#define B3   247
#define C4   262
#define D4   294
#define DS4  311
#define E4   330
#define F4   349
#define G4   392
#define A4   440
#define B4   494
#define C5   523
#define D5n  587
#define DS5  622
#define E5   659
#define G5   784
#define A5   880
#define C6  1047
#define D6  1175
#define E6  1319
#define G6  1568

struct Note { uint16_t freq; uint16_t ms; };

/* =======================================================
   THE JINGLES
   Keep every one under ~1.5 s or it fights the narration.
   ======================================================= */

const Note sndBoot[]        = {{C5,70},{E5,70},{G5,120}};

// Low war-drum thuds climbing into a held, uneasy note.
const Note sndCombatStart[] = {{G3,110},{R,60},{G3,110},{R,60},
                               {AS3,110},{R,40},{C4,140},{R,30},{DS4,380}};

const Note sndCombatWin[]   = {{C5,110},{E5,110},{G5,110},{C6,300}};
const Note sndCombatLose[]  = {{G4,160},{E4,160},{C4,200},{G3,450}};
const Note sndCombatEnd[]   = {{G4,90},{C5,180}};

// Very short high-to-low zap — a clean strike.
const Note sndAttackHit[]   = {{G6,25},{D6,25},{G5,45}};
const Note sndAttackMiss[]  = {{A4,40},{F4,70}};

const Note sndCrit[]        = {{G5,45},{C6,45},{E6,45},{G6,180}};
const Note sndFumble[]      = {{B3,60},{A3,60},{G3,60},{D3n,220}};

// Ugly downward slide, ends low and flat.
const Note sndDamageTaken[] = {{DS4,55},{B3,55},{G3,70},{D3n,170}};
const Note sndHeal[]        = {{C5,60},{E5,60},{G5,60},{C6,140}};

// Two notes a tritone apart, flipping — the classic "wrong" interval.
const Note sndTrapFound[]   = {{A5,90},{DS5,90},{A5,90},{DS5,90},{A5,90},{DS5,200}};

// Bright ladder up with a sparkle on top.
const Note sndTreasure[]    = {{C5,70},{E5,70},{G5,70},{C6,140},{R,40},{E6,200}};

const Note sndLevelUp[]     = {{C5,80},{D5n,80},{E5,80},{G5,80},{C6,260}};
const Note sndDiceLanded[]  = {{E6,18},{C6,30}};
const Note sndListenStart[] = {{A4,45},{E5,70}};
const Note sndListenEnd[]   = {{E5,45},{A4,70}};
const Note sndError[]       = {{A3,90},{R,40},{A3,160}};

/* ---------------- cue table ----------------
 * priority: a lower-priority cue is dropped while a higher one plays.
 * A dice tick can't cut off the victory fanfare; a crit can cut off a hit.
 */
struct Cue { const Note* notes; uint8_t count; uint8_t priority; };
#define C(arr, pri) { arr, (uint8_t)(sizeof(arr)/sizeof(arr[0])), pri }

enum SoundId {
  SND_STOP = 0, SND_BOOT, SND_COMBAT_START, SND_COMBAT_WIN, SND_COMBAT_LOSE,
  SND_COMBAT_END, SND_ATTACK_HIT, SND_ATTACK_MISS, SND_CRIT, SND_FUMBLE,
  SND_DAMAGE_TAKEN, SND_HEAL, SND_TRAP_FOUND, SND_TREASURE_FOUND,
  SND_LEVEL_UP, SND_DICE_LANDED, SND_LISTEN_START, SND_LISTEN_END,
  SND_ERROR, CUE_COUNT
};

const Cue CUES[CUE_COUNT] = {
  C(sndBoot, 0),            // 0 unused, SND_STOP handled separately
  C(sndBoot, 1),            // 1  boot
  C(sndCombatStart, 5),     // 2  combat_start
  C(sndCombatWin, 5),       // 3  combat_win
  C(sndCombatLose, 5),      // 4  combat_lose
  C(sndCombatEnd, 4),       // 5  combat_end
  C(sndAttackHit, 2),       // 6  attack_hit
  C(sndAttackMiss, 2),      // 7  attack_miss
  C(sndCrit, 3),            // 8  crit
  C(sndFumble, 3),          // 9  fumble
  C(sndDamageTaken, 2),     // 10 damage_taken
  C(sndHeal, 2),            // 11 heal
  C(sndTrapFound, 4),       // 12 trap_found
  C(sndTreasure, 4),        // 13 treasure_found
  C(sndLevelUp, 5),         // 14 level_up
  C(sndDiceLanded, 1),      // 15 dice_landed
  C(sndListenStart, 1),     // 16 listen_start
  C(sndListenEnd, 1),       // 17 listen_end
  C(sndError, 3)            // 18 error
};

/* ---------------- buzzer state machine ---------------- */
static const Cue* g_cue      = nullptr;
static uint8_t    g_noteIdx  = 0;
static uint8_t    g_priority = 0;
static uint32_t   g_noteStart = 0;

static void startNote(uint32_t now) {
  uint16_t f = g_cue->notes[g_noteIdx].freq;
  if (f == 0) noTone(BUZZER_PIN); else tone(BUZZER_PIN, f);
  g_noteStart = now;
}

void stopSound() {
  noTone(BUZZER_PIN);
  g_cue = nullptr;
  g_priority = 0;
}

bool soundBusy() { return g_cue != nullptr; }

void playSound(int id) {
  if (id <= 0 || id >= CUE_COUNT) { stopSound(); return; }
  const Cue& c = CUES[id];
  if (g_cue != nullptr && c.priority < g_priority) return;   // let the big one finish
  g_cue = &c;
  g_priority = c.priority;
  g_noteIdx = 0;
  startNote(millis());
}

void updateSound() {
  if (g_cue == nullptr) return;
  uint32_t now = millis();
  if (now - g_noteStart < g_cue->notes[g_noteIdx].ms) return;
  g_noteIdx++;
  if (g_noteIdx >= g_cue->count) { stopSound(); return; }
  startNote(now);
}

/* ---------------- combat light ---------------- */
static bool g_lightOn = false;

void setLight(bool on) {
  g_lightOn = on;
  digitalWrite(LIGHT_PIN, on ? HIGH : LOW);
}

/* ---------------- dice tray piezo ---------------- */
const int      KNOCK_THRESHOLD = 90;    // raise if it self-triggers
const uint32_t KNOCK_DEBOUNCE_MS = 300; // a die bounces several times
const uint32_t BUZZER_BLANK_MS = 120;   // ignore the tray just after a jingle

static uint32_t g_lastKnockMs = 0;
static uint32_t g_lastSoundMs = 0;

void updateTray() {
  uint32_t now = millis();

  // The buzzer is bolted to the same table as the tray. If we read the piezo
  // while a jingle plays, the buzzer's own vibration fakes a dice hit.
  if (soundBusy()) { g_lastSoundMs = now; return; }
  if (now - g_lastSoundMs < BUZZER_BLANK_MS) return;

  if (now - g_lastKnockMs < KNOCK_DEBOUNCE_MS) return;

  if (analogRead(PIEZO_PIN) > KNOCK_THRESHOLD) {
    g_lastKnockMs = now;
    Bridge.notify("dice_landed");
    playSound(SND_DICE_LANDED);
  }
}

/* ---------------- push-to-talk button ---------------- */
const uint32_t BTN_DEBOUNCE_MS = 50;

static bool     g_btnStable = HIGH;     // HIGH = released
static bool     g_btnLast   = HIGH;
static uint32_t g_btnChanged = 0;

bool button_state() { return g_btnStable == LOW; }

void updateButton() {
  bool reading = digitalRead(BUTTON_PIN);
  if (reading != g_btnLast) { g_btnLast = reading; g_btnChanged = millis(); }
  if (reading != g_btnStable && (millis() - g_btnChanged) >= BTN_DEBOUNCE_MS) {
    g_btnStable = reading;
    bool pressed = (g_btnStable == LOW);
    Bridge.notify("button_event", pressed);
    playSound(pressed ? SND_LISTEN_START : SND_LISTEN_END);
  }
}

/* ---------------- Bridge handlers ---------------- */
static void onDmSound(int id)      { playSound(id); }
static void onDmSoundStop()        { stopSound(); }
static void onDmLight(bool on)     { setLight(on); }

void setup() {
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LIGHT_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  setLight(false);

  Bridge.begin();
  Monitor.begin(115200);

  Bridge.provide("dm_sound", onDmSound);
  Bridge.provide("dm_sound_stop", onDmSoundStop);
  Bridge.provide("dm_light", onDmLight);
  Bridge.provide_safe("button_state", button_state);

  Monitor.println("Edge-DM cue layer ready");
  playSound(SND_BOOT);
}

void loop() {
  updateSound();
  updateButton();
  updateTray();
  // No delay() — everything above is millis()-based and must stay that way.
}
