/*
 * Edge-DM — MCU firmware (STM32 core of the Arduino UNO Q)
 *
 * Push-to-talk button. Reads a momentary button on D2, debounces it, and
 * tells the Linux core when it goes down and when it comes back up.
 *
 * Exposes two things to Linux over the Bridge:
 *   button_event(bool pressed)  — NOTIFICATION pushed on every debounced edge
 *   button_state() -> bool      — CALLABLE, returns the current debounced state
 *
 * Both exist on purpose: voice_loop.py uses the event path by default and can
 * fall back to polling button_state if the event path misbehaves.
 *
 * WIRING
 *   Button leg 1 -> D2
 *   Button leg 2 -> GND
 *   No external resistor. INPUT_PULLUP means the pin idles HIGH and reads LOW
 *   when the button is held, so "pressed" is an ACTIVE-LOW reading.
 */

#include <Arduino_RouterBridge.h>

const int BUTTON_PIN = 2;
const unsigned long DEBOUNCE_MS = 50;

static bool stableState  = HIGH;   // HIGH = released (INPUT_PULLUP)
static bool lastReading  = HIGH;
static unsigned long lastChangeMs = 0;

/* Callable from Linux: Bridge.call("button_state") */
bool button_state() {
  return stableState == LOW;
}

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  Bridge.begin();
  Monitor.begin(115200);

  // provide_safe runs the callback from the main loop thread, so it can read
  // stableState without racing the debounce code below.
  Bridge.provide_safe("button_state", button_state);

  Monitor.println("Edge-DM button firmware ready");
}

void loop() {
  bool reading = digitalRead(BUTTON_PIN);

  // Any change restarts the debounce timer.
  if (reading != lastReading) {
    lastReading  = reading;
    lastChangeMs = millis();
  }

  // Accept the new level only once it has held steady past DEBOUNCE_MS.
  if (reading != stableState && (millis() - lastChangeMs) >= DEBOUNCE_MS) {
    stableState = reading;
    bool pressed = (stableState == LOW);

    // notify, not call — we want fire-and-forget with no return value.
    // Using call here would block this loop waiting on a response.
    Bridge.notify("button_event", pressed);

    Monitor.print("button ");
    Monitor.println(pressed ? "DOWN" : "UP");
  }

  // No delay(). The loop must stay free-running so the debounce timing stays
  // accurate and the Bridge can service incoming button_state calls.
}
