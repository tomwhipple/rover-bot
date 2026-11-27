// rover-cam firmware: aLaMode (ATmega328P) + Adafruit Motor Shield v1.
// Glue around rover_core; the protocol and limits live there.
// Protocol: docs/serial-protocol.md.
#include <AFMotor.h>
#include <Servo.h>

#include "rover_core.h"

// 3-pin Ping))) on the motor shield's S1 header (D10): trigger and echo share the pin.
// A 4-pin HC-SR04 takes two pins instead (the design's A1 trigger, A2 echo).
const int PIN_SERVO = 9, PIN_VBAT = A0, PIN_TRIG = 10, PIN_ECHO = 10;

// Pan servo calibration, measured on the rover with firmware/pan_cal: the pulse width that points
// the camera straight ahead, and the change in pulse width for 90 degrees to the left (9.8 degrees
// per 100 us, from camera images across 1450..2400 us). The edges the pan stops at are
// PAN_EDGE_LEFT/RIGHT in rover_core.h.
const int PAN_CENTER_US = 1450;
const int PAN_US_PER_90 = 918;

const unsigned long PING_PERIOD_US = 60000UL;
const unsigned long PING_TIMEOUT_US = 25000UL;  // ~4.3 m, as pulseIn had
const unsigned long PACK_PERIOD_MS = 20;

AF_DCMotor leftMotor(3), rightMotor(4);  // M3/M4: PWM on D5/D6, keeps D11 free
Servo pan;
rover::Core core;

long packMv() {  // 10k over 3.3k divider on A0
  return (long)analogRead(PIN_VBAT) * 5000 / 1023 * 133 / 33;
}

// Non-blocking range: trigger, then poll the echo pin once per loop pass.
enum PingState { PING_IDLE, PING_WAIT_RISE, PING_WAIT_FALL };
PingState pingState = PING_IDLE;
unsigned long pingStartUs = 0, pingRiseUs = 0;
uint16_t rangeCm = rover::NO_ECHO_CM;  // 999 = no echo

void servicePing() {
  unsigned long now = micros();
  switch (pingState) {
    case PING_IDLE:
      if (now - pingStartUs < PING_PERIOD_US) return;
      pinMode(PIN_TRIG, OUTPUT);
      digitalWrite(PIN_TRIG, HIGH);
      delayMicroseconds(10);
      digitalWrite(PIN_TRIG, LOW);
      pinMode(PIN_ECHO, INPUT);
      pingStartUs = micros();
      pingState = PING_WAIT_RISE;
      return;
    case PING_WAIT_RISE:
      if (digitalRead(PIN_ECHO)) {
        pingRiseUs = now;
        pingState = PING_WAIT_FALL;
      } else if (now - pingStartUs > PING_TIMEOUT_US) {
        rangeCm = rover::NO_ECHO_CM;
        pingState = PING_IDLE;
      }
      return;
    case PING_WAIT_FALL:
      if (!digitalRead(PIN_ECHO)) {
        unsigned long cm = (now - pingRiseUs) / 58;
        rangeCm = cm > rover::NO_ECHO_CM ? rover::NO_ECHO_CM : (uint16_t)cm;
        pingState = PING_IDLE;
      } else if (now - pingStartUs > PING_TIMEOUT_US) {
        rangeCm = rover::NO_ECHO_CM;
        pingState = PING_IDLE;
      }
      return;
  }
}

int panUs(int8_t deg) {  // deg is -90..90, + to the left
  return PAN_CENTER_US + (int)((long)deg * PAN_US_PER_90 / 90);
}

void spin(AF_DCMotor &m, int v) {
  m.setSpeed(v < 0 ? -v : v);
  m.run(v > 0 ? FORWARD : v < 0 ? BACKWARD : RELEASE);
}

void setup() {
  Serial.begin(115200);
  pan.writeMicroseconds(panUs(rover::PAN_DEFAULT));
  pan.attach(PIN_SERVO);
  spin(leftMotor, 0);
  spin(rightMotor, 0);
}

void loop() {
  static unsigned long lastLoopUs = 0, packAt = 0;
  static uint16_t packReading = 0;
  static int16_t appliedL = 0, appliedR = 0;
  static int8_t appliedPan = rover::PAN_DEFAULT;
  unsigned long startUs = micros();

  while (Serial.available()) core.rx((uint8_t)Serial.read());

  servicePing();
  unsigned long ms = millis();
  if (ms - packAt >= PACK_PERIOD_MS || packAt == 0) {
    packAt = ms ? ms : 1;
    long mv = packMv();
    packReading = mv > 0xFFFF ? 0xFFFF : (uint16_t)mv;
  }

  rover::Inputs in = {ms, packReading, rangeCm, (uint32_t)lastLoopUs};
  rover::Outputs out = core.tick(in);

  if (out.left != appliedL) { appliedL = out.left; spin(leftMotor, appliedL); }
  if (out.right != appliedR) { appliedR = out.right; spin(rightMotor, appliedR); }
  if (out.pan != appliedPan) { appliedPan = out.pan; pan.writeMicroseconds(panUs(appliedPan)); }

  while (Serial.availableForWrite() > 0 && core.tx_pending()) Serial.write((uint8_t)core.tx_pop());

  lastLoopUs = micros() - startUs;
}
