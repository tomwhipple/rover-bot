// rover-cam firmware: aLaMode (ATmega328P) + Adafruit Motor Shield v1.
// Line commands at 115200 baud:  M <left> <right> <ms> | P <deg> | S | ?
// Limits live here so nothing upstream can override them.
#include <AFMotor.h>
#include <Servo.h>

const int MAX_PWM = 150;        // ~60 % of 255
const int MAX_MS = 2000;        // longest single move
const int STANDOFF_CM = 80;     // biped is 56 cm tall; stay out of its fall
const long MIN_PACK_MV = 10500; // no driving below 3.5 V/cell

// HC-SR04: trigger A1, echo A2. 3-pin Ping))): set both to A1.
const int PIN_SERVO = 9, PIN_VBAT = A0, PIN_TRIG = A1, PIN_ECHO = A2;

AF_DCMotor left(3), right(4);   // M3/M4: PWM on D5/D6, keeps D11 free
Servo pan;
unsigned long stopAt = 0;
bool moving = false, forward = false;

long packMv() {                 // 10k over 3.3k divider on A0
  return (long)analogRead(PIN_VBAT) * 5000 / 1023 * 133 / 33;
}

int rangeCm() {                 // 999 = no echo (nothing within ~4 m)
  pinMode(PIN_TRIG, OUTPUT);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT);
  unsigned long us = pulseIn(PIN_ECHO, HIGH, 25000);
  return us ? us / 58 : 999;
}

void spin(AF_DCMotor &m, int v) {
  m.setSpeed(min(abs(v), MAX_PWM));
  m.run(v > 0 ? FORWARD : v < 0 ? BACKWARD : RELEASE);
}

void stopMotors() {
  spin(left, 0);
  spin(right, 0);
  moving = false;
}

void handle(char *line) {
  int a, b, c;
  if (line[0] == 'M' && sscanf(line + 1, "%d %d %d", &a, &b, &c) == 3) {
    if (packMv() < MIN_PACK_MV) { Serial.println("ERR low pack"); return; }
    forward = a + b > 0;
    if (forward && rangeCm() < STANDOFF_CM) { Serial.println("ERR blocked"); return; }
    spin(left, a);
    spin(right, b);
    stopAt = millis() + constrain(c, 0, MAX_MS);
    moving = true;
    Serial.println("OK");
  } else if (line[0] == 'P' && sscanf(line + 1, "%d", &a) == 1) {
    pan.write(constrain(a, 0, 180));
    Serial.println("OK");
  } else if (line[0] == 'S') {
    stopMotors();
    Serial.println("OK");
  } else if (line[0] == '?') {
    Serial.print("mv=");
    Serial.print(packMv());
    Serial.print(" cm=");
    Serial.println(rangeCm());
  } else {
    Serial.println("ERR bad command");
  }
}

void setup() {
  Serial.begin(115200);
  pan.attach(PIN_SERVO);
  pan.write(90);
  stopMotors();
}

void loop() {
  static char buf[32];
  static byte n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n') { buf[n] = 0; n = 0; handle(buf); }
    else if (n < sizeof(buf) - 1) buf[n++] = ch;
  }
  if (moving && ((long)(millis() - stopAt) >= 0 ||
                 (forward && rangeCm() < STANDOFF_CM)))
    stopMotors();
}
