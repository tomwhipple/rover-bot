// rover-cam firmware: aLaMode (ATmega328P) + Adafruit Motor Shield v1.
// Glue around rover_core; the protocol and limits live there.
// Protocol: docs/serial-protocol.md.
#include <AFMotor.h>

#include "rover_core.h"

// The pan servo is on the motor shield's S2 header (D9) and the 3-pin Ping))) on S1 (D10). Both pins
// are fixed by the hardware they use: Timer1's OC1A, and pin-change interrupt PCINT2.
const int PIN_SERVO = 9, PIN_VBAT = A0;

// Pan servo calibration, measured on the rover with firmware/pan_cal: the pulse width that points
// the camera straight ahead, and the change in pulse width for 90 degrees to the left (9.8 degrees
// per 100 us, from camera images across 1450..2400 us). The edges the pan stops at are
// PAN_EDGE_LEFT/RIGHT in rover_core.h.
const int PAN_CENTER_US = 1450;
const int PAN_US_PER_90 = 918;

const unsigned long PING_PERIOD_US = 60000UL;
const unsigned long PING_TIMEOUT_US = 25000UL;  // from the trigger: an echo from about 4 m
const unsigned int PING_TRIGGER_US = 5;
const unsigned int PING_TRIGGER_TAIL_US = 10;   // low this long after the trigger before letting go
const unsigned long PING_MIN_ECHO_US = 100;     // real echoes start at 115 us (2 cm)
const unsigned long PING_US_PER_CM = 58;        // round trip at 345 m/s
const unsigned long PACK_PERIOD_MS = 20;

AF_DCMotor leftMotor(3), rightMotor(4);  // M3/M4: PWM on D5/D6, keeps D11 free
rover::Core core;

long packMv() {  // 10k over 3.3k divider on A0
  return (long)analogRead(PIN_VBAT) * 5000 / 1023 * 133 / 33;
}

// Range from the Ping))): its one signal pin is D10 (PB2, pin-change interrupt PCINT2). A ping is a
// short high pulse from here; 750 us later the sensor holds the pin high for the echo's round trip.
// The two edges are timed in the interrupt handler, to micros()'s 4 us. Outside that listening
// window the pin is driven low: left floating, the servo pulse on D9 couples into the wire and
// triggers the sensor again, 50 times a second.
enum PingState { PING_IDLE, PING_LISTEN };
PingState pingState = PING_IDLE;
unsigned long pingStartUs = 0;
volatile unsigned long pingRiseUs = 0, pingFallUs = 0;
volatile uint8_t pingEdges = 0;        // 0 none, 1 rise seen, 2 fall seen
uint16_t rangeCm = rover::NO_ECHO_CM;  // 999 = no echo

inline void pingHoldLow() {
  PCMSK0 &= ~_BV(PCINT2);
  PORTB &= ~_BV(PB2);
  DDRB |= _BV(PB2);
}

ISR(PCINT0_vect) {
  const unsigned long t = micros();
  const bool high = PINB & _BV(PB2);
  if (high && pingEdges == 0) {
    pingRiseUs = t;
    pingEdges = 1;
  } else if (!high && pingEdges == 1) {
    pingFallUs = t;
    pingEdges = 2;
    pingHoldLow();
  }
}

void pingBegin() {
  pingHoldLow();
  PCICR |= _BV(PCIE0);
}

void servicePing() {
  const unsigned long now = micros();
  if (pingState == PING_IDLE) {
    if (now - pingStartUs < PING_PERIOD_US) return;
    PORTB |= _BV(PB2);  // the pin is an output, low, between pings
    delayMicroseconds(PING_TRIGGER_US);
    PORTB &= ~_BV(PB2);
    // Released at once, the sensor misses up to a quarter of the triggers.
    delayMicroseconds(PING_TRIGGER_TAIL_US);
    DDRB &= ~_BV(PB2);
    pingEdges = 0;
    pingStartUs = micros();
    PCIFR = _BV(PCIF0);
    PCMSK0 |= _BV(PCINT2);
    pingState = PING_LISTEN;
    return;
  }
  noInterrupts();
  const uint8_t edges = pingEdges;
  const unsigned long us = pingFallUs - pingRiseUs;
  interrupts();
  if (edges == 2) {
    // Shorter than the sensor's shortest echo: it is blinded by something against its face.
    rangeCm = us < PING_MIN_ECHO_US ? 0 : (uint16_t)min((us + PING_US_PER_CM / 2) / PING_US_PER_CM,
                                                        (unsigned long)rover::NO_ECHO_CM);
    pingState = PING_IDLE;
  } else if (now - pingStartUs > PING_TIMEOUT_US) {
    pingHoldLow();
    rangeCm = rover::NO_ECHO_CM;
    pingState = PING_IDLE;
  }
}

int panUs(int8_t deg) {  // deg is -90..90, + to the left
  return PAN_CENTER_US + (int)((long)deg * PAN_US_PER_90 / 90);
}

// The pan pulse comes from Timer1's output compare on D9 (OC1A), not from the Servo library: a
// pulse timed in an interrupt handler is late by a few microseconds whenever another interrupt
// (serial, millis) is running, and the servo ticks at each one. Fast PWM with ICR1 as top: 20 ms
// period, 0.5 us a count at 16 MHz. D10 (OC1B) is left an ordinary pin, for the Ping))).
void panWriteUs(int us) { OCR1A = (uint16_t)(us * 2 - 1); }

void panBegin(int us) {
  TCCR1A = 0;
  TCCR1B = 0;
  ICR1 = 39999;
  panWriteUs(us);
  TCCR1A = _BV(COM1A1) | _BV(WGM11);
  TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS11);
  pinMode(PIN_SERVO, OUTPUT);
}

void spin(AF_DCMotor &m, int v) {
  m.setSpeed(v < 0 ? -v : v);
  m.run(v > 0 ? FORWARD : v < 0 ? BACKWARD : RELEASE);
}

void setup() {
  Serial.begin(115200);
  panBegin(panUs(rover::PAN_DEFAULT));
  pingBegin();
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
  if (out.pan != appliedPan) { appliedPan = out.pan; panWriteUs(panUs(appliedPan)); }

  while (Serial.availableForWrite() > 0 && core.tx_pending()) Serial.write((uint8_t)core.tx_pop());

  lastLoopUs = micros() - startUs;
}
