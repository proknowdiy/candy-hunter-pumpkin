
/*
 * Candy-Hunter Pumpkin
 * ESP32-C3 Controller Firmware
 * =============================
 *
 * Created by: Pro Know
 * YouTube: https://www.youtube.com/@ProKnow
 * Hardware: ESP32-C3
 *
 * Copyright (c) 2026 Pro Know
 * Licensed under the MIT License. See LICENSE in the project root.
 *
 * PROJECT DESCRIPTION
 * -------------------
 * Controls the interactive pumpkin mechanism for the Candy-Hunter
 * Pumpkin project. The RDK X5 performs AI candy detection and
 * communicates with this controller over USB serial.
 *
 * LIBRARIES
 * ---------
 * - FastLED[ Version - 3.9.20 ]: LED control and effects
 * - ESP32Servo [ Version - 3.2.1 ]: Servo motor control
 *
 * Install both libraries through Arduino IDE Library Manager.
 *
 * COMMUNICATION
 * -------------
 * Interface: USB Serial
 * Baud rate: 115200
 *
 * The firmware must remain compatible with the commands and state
 * messages exchanged with the Python application on the RDK X5.
 *
 * SAFETY
 * ------
 * Check servo power requirements and LED power consumption before
 * connecting the hardware. Do not power high-current loads directly
 * from the ESP32-C3 board.
 *
 * See the project README.md for wiring, setup, and upload instructions.
 */



#include <FastLED.h>
#include <ESP32Servo.h>

// =====================================================
// PIN CONFIGURATION
// =====================================================
#define LED_DATA_PIN  0
#define SERVO_PIN     6

// All LEDs are chained in this order:
// Right eye DO -> Left eye DI
// Left eye DO  -> Mouth DI

#define RIGHT_EYE_COUNT  8
#define LEFT_EYE_COUNT   8
#define MOUTH_LED_COUNT 12
#define TOTAL_LEDS (RIGHT_EYE_COUNT + LEFT_EYE_COUNT + MOUTH_LED_COUNT)

// =====================================================
// USER CONFIGURATION
// Change these values as required
// =====================================================
const uint8_t LED_BRIGHTNESS = 100;

// Initial servo values; calibrate with the linkage disconnected.
const int SERVO_CLOSED_ANGLE = 90;
const int SERVO_EVIL_ANGLE   = 50;
const int SERVO_OPEN_ANGLE   = 160;

//const unsigned long BLINK_INTERVAL_MS = 1000;
const unsigned long BLINK_INTERVAL_MS = 3000;  // Time between blinks
const unsigned long BLINK_DURATION_MS = 150;   // How long eyes stay blinked
const unsigned long COLOR_FADE_MS     = 1000;

const unsigned long SERVO_STEP_MS = 15;
const int SERVO_STEP_DEGREES = 1;

const unsigned long EVIL_HOLD_MS = 500;
const unsigned long MOUTH_OPEN_MS = 5000;
const unsigned long COOLDOWN_MS = 10000;

// =====================================================
// LEDS AND SERVO
// =====================================================
CRGB leds[TOTAL_LEDS];
Servo mouthServo;

const CRGB ORANGE = CRGB(255, 70, 0);
const CRGB GREEN  = CRGB(0, 255, 20);

CRGB fadeFrom = ORANGE;
CRGB fadeTo   = ORANGE;
unsigned long fadeStarted = 0;

int servoCurrent = SERVO_CLOSED_ANGLE;
int servoTarget  = SERVO_CLOSED_ANGLE;

// =====================================================
// ROBOT MODE AND PHASE
// =====================================================
enum RobotMode {
  STANDBY_MODE,
  HUNT_MODE
};

enum RobotPhase {
  IDLE_PHASE,
  HUNTING_PHASE,
  OPENING_PHASE,
  OPEN_HOLD_PHASE,
  CLOSING_PHASE,
  COOLDOWN_PHASE
};

RobotMode mode = STANDBY_MODE;
RobotPhase phase = IDLE_PHASE;

unsigned long lastBlink = 0;
unsigned long blinkStarted = 0;
unsigned long lastServoStep = 0;
unsigned long phaseStarted = 0;

bool blinkOn = true;
bool evilAtEnd = false;

// =====================================================
// COLOR TRANSITION
// =====================================================
CRGB getCurrentColor() {
  unsigned long elapsed = millis() - fadeStarted;

  if (COLOR_FADE_MS == 0 || elapsed >= COLOR_FADE_MS) {
    return fadeTo;
  }

  float t = (float)elapsed / (float)COLOR_FADE_MS;

  CRGB result;
  result.r = (uint8_t)(fadeFrom.r +
             ((int)fadeTo.r - (int)fadeFrom.r) * t);
  result.g = (uint8_t)(fadeFrom.g +
             ((int)fadeTo.g - (int)fadeFrom.g) * t);
  result.b = (uint8_t)(fadeFrom.b +
             ((int)fadeTo.b - (int)fadeFrom.b) * t);

  return result;
}

void setColorTarget(const CRGB &color) {
  fadeFrom = getCurrentColor();
  fadeTo = color;
  fadeStarted = millis();
}

// =====================================================
// LED ANIMATION
// LEDs 0-7:   Right eye
// LEDs 8-15:  Left eye
// LEDs 16-27: Mouth
//
// First 4 LEDs of each eye remain on.
// Last 4 LEDs blink.
// =====================================================
void updateLeds() {
  CRGB color = getCurrentColor();

  // Right eye: LEDs 0-7
  for (int i = 0; i < RIGHT_EYE_COUNT; i++) {
    leds[i] = (i < 4 || blinkOn)
                ? color : CRGB::Black;
  }

  // Left eye: LEDs 8-15
  for (int i = 0; i < LEFT_EYE_COUNT; i++) {
    int index = RIGHT_EYE_COUNT + i;

    leds[index] = (i < 4 || blinkOn)
                    ? color : CRGB::Black;
  }

  // Mouth: LEDs 16-27
  for (int i = 0; i < MOUTH_LED_COUNT; i++) {
    int index = RIGHT_EYE_COUNT + LEFT_EYE_COUNT + i;
    leds[index] = color;
  }

  FastLED.show();
}

// void updateBlink() {
//   unsigned long now = millis();

//   if (now - lastBlink >= BLINK_INTERVAL_MS) {
//     lastBlink = now;
//     blinkOn = !blinkOn;
//   }
// }

void updateBlink() {
  unsigned long now = millis();

  if (blinkOn) {
    // Eyes are open: wait until the next blink.
    if (now - lastBlink >= BLINK_INTERVAL_MS) {
      blinkOn = false;
      blinkStarted = now;
    }
  } else {
    // Eyes are closed: reopen after blink duration.
    if (now - blinkStarted >= BLINK_DURATION_MS) {
      blinkOn = true;
      lastBlink = now;
    }
  }
}

// =====================================================
// SERVO CONTROL
// Gradual movement instead of jumping directly
// =====================================================
void updateServo() {
  unsigned long now = millis();

  if (now - lastServoStep < SERVO_STEP_MS) {
    return;
  }

  lastServoStep = now;

  if (servoCurrent < servoTarget) {
    servoCurrent += SERVO_STEP_DEGREES;

    if (servoCurrent > servoTarget) {
      servoCurrent = servoTarget;
    }
  } else if (servoCurrent > servoTarget) {
    servoCurrent -= SERVO_STEP_DEGREES;

    if (servoCurrent < servoTarget) {
      servoCurrent = servoTarget;
    }
  }

  mouthServo.write(servoCurrent);
}

bool servoAtTarget() {
  return servoCurrent == servoTarget;
}

// =====================================================
// MODE CONTROL
// =====================================================
void enterStandby() {
  mode = STANDBY_MODE;
  phase = IDLE_PHASE;

  setColorTarget(ORANGE);
  servoTarget = SERVO_CLOSED_ANGLE;

  Serial.println("STATE:STANDBY");
}

void enterHunt() {
  mode = HUNT_MODE;
  phase = HUNTING_PHASE;

  evilAtEnd = false;
  phaseStarted = millis();

  setColorTarget(GREEN);
  servoTarget = SERVO_CLOSED_ANGLE;

  Serial.println("STATE:HUNT");
}

void startCandyReaction() {
  if (mode != HUNT_MODE || phase != HUNTING_PHASE) {
    Serial.println("CANDY_IGNORED");
    return;
  }

  phase = OPENING_PHASE;
  servoTarget = SERVO_OPEN_ANGLE;

  Serial.println("STATE:OPENING");
}

// =====================================================
// STATE MACHINE
// =====================================================
void updateStateMachine() {
  unsigned long now = millis();

  switch (phase) {
    case IDLE_PHASE:
      servoTarget = SERVO_CLOSED_ANGLE;
      break;

    case HUNTING_PHASE:
      // Alternate between closed and evil-mouth positions.
      if (now - phaseStarted >= EVIL_HOLD_MS) {
        phaseStarted = now;
        evilAtEnd = !evilAtEnd;

        servoTarget = evilAtEnd
                        ? SERVO_EVIL_ANGLE
                        : SERVO_CLOSED_ANGLE;
      }
      break;

    case OPENING_PHASE:
      if (servoAtTarget()) {
        phase = OPEN_HOLD_PHASE;
        phaseStarted = now;
        Serial.println("STATE:MOUTH_OPEN");
      }
      break;

    case OPEN_HOLD_PHASE:
      if (now - phaseStarted >= MOUTH_OPEN_MS) {
        phase = CLOSING_PHASE;
        servoTarget = SERVO_CLOSED_ANGLE;
        Serial.println("STATE:CLOSING");
      }
      break;

    case CLOSING_PHASE:
      if (servoAtTarget()) {
        setColorTarget(ORANGE);
        phase = COOLDOWN_PHASE;
        phaseStarted = now;
        Serial.println("STATE:COOLDOWN");
      }
      break;

    case COOLDOWN_PHASE:
      if (now - phaseStarted >= COOLDOWN_MS) {
        if (mode == HUNT_MODE) {
          enterHunt();
        } else {
          phase = IDLE_PHASE;
        }
      }
      break;
  }
}

// =====================================================
// USB SERIAL COMMANDS
//
// STANDBY
// HUNT
// CANDY
// RESET
// STATUS
// =====================================================
void handleSerial() {
  static String command;

  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      command.trim();
      command.toUpperCase();

      if (command == "STANDBY") {
        enterStandby();
      }
      else if (command == "HUNT") {
        enterHunt();
      }
      else if (command == "CANDY") {
        startCandyReaction();
      }
      else if (command == "RESET") {
        enterStandby();
      }
      else if (command == "STATUS") {
        Serial.print("MODE:");
        Serial.print(mode == STANDBY_MODE ? "STANDBY" : "HUNT");
        Serial.print(",PHASE:");
        Serial.println((int)phase);
      }
      else if (command.length() > 0) {
        Serial.println("ERROR:UNKNOWN_COMMAND");
      }

      command = "";
    }
    else if (command.length() < 40) {
      command += c;
    }
  }
}

// =====================================================
// SETUP
// =====================================================
void setup() {
  Serial.begin(115200);

  // One FastLED controller for all 28 LEDs.
  FastLED.addLeds<WS2812B, LED_DATA_PIN, GRB>(
    leds, TOTAL_LEDS
  );

  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear(true);

  mouthServo.setPeriodHertz(50);
  mouthServo.attach(SERVO_PIN, 500, 2400);

  servoCurrent = SERVO_CLOSED_ANGLE;
  servoTarget = SERVO_CLOSED_ANGLE;
  mouthServo.write(servoCurrent);

  fadeFrom = ORANGE;
  fadeTo = ORANGE;
  fadeStarted = millis();

  enterStandby();

  Serial.println("PUMPKIN_READY");
}

// =====================================================
// MAIN LOOP
// =====================================================
void loop() {
  handleSerial();
  updateBlink();
  updateStateMachine();
  updateServo();
  updateLeds();
}
