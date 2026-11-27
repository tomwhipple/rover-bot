// Pan servo calibration for the rover: flash it in place of firmware/rover, find the pulse widths
// for straight ahead and for each end of the travel, then put them in rover.ino and rover_core.h.
// The motors stay released. 115200 baud, one command a line:
//
//   U <us>   move to a pulse width, 500..2500, at 200 us a second (about 20 degrees a second)
//   +  -     10 us more / less
//   X        stop where it is
//   ?        report
//
// Every line is answered with "us=<now> target=<target>".
//
// pancal.py (run on the Pi) sends one command; sweep.py (run on the laptop) steps through the travel
// and measures each step from the rover's camera.
#include <AFMotor.h>
#include <Servo.h>

const int PIN_SERVO = 9;
const int US_MIN = 500, US_MAX = 2500, US_START = 1500;
const unsigned long STEP_MS = 5;  // 1 us every 5 ms

AF_DCMotor leftMotor(3), rightMotor(4);
Servo pan;
int nowUs = US_START, targetUs = US_START;
unsigned long stepAt = 0;

void report() {
  Serial.print("us=");
  Serial.print(nowUs);
  Serial.print(" target=");
  Serial.println(targetUs);
}

void handle(char *line) {
  if (line[0] == 'U') targetUs = constrain(atoi(line + 1), US_MIN, US_MAX);
  else if (line[0] == '+') targetUs = constrain(targetUs + 10, US_MIN, US_MAX);
  else if (line[0] == '-') targetUs = constrain(targetUs - 10, US_MIN, US_MAX);
  else if (line[0] == 'X') targetUs = nowUs;
  report();
}

void setup() {
  Serial.begin(115200);
  leftMotor.run(RELEASE);
  rightMotor.run(RELEASE);
  pan.writeMicroseconds(nowUs);
  pan.attach(PIN_SERVO);
  report();
}

void loop() {
  static char buf[16];
  static byte n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n') { buf[n] = 0; n = 0; if (buf[0]) handle(buf); }
    else if (ch != '\r' && n < sizeof(buf) - 1) buf[n++] = ch;
  }
  unsigned long ms = millis();
  if (nowUs != targetUs && ms - stepAt >= STEP_MS) {
    stepAt = ms;
    nowUs += nowUs < targetUs ? 1 : -1;
    pan.writeMicroseconds(nowUs);
  }
}
