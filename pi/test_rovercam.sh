#!/bin/sh
# rovercam against a stand-in rpicam-vid: the stream's framing, /snap.jpg, and rover snap through it.
set -eu
t=build/test/rovercam
rm -rf "$t"; mkdir -p "$t/bin"
port=$((20000 + $$ % 20000))
printf 'camera_rotation = 180\nstream_port = %s\n' "$port" > "$t/rover.conf"
# The stand-in writes its arguments down, then five-byte "JPEGs" (FF D8 x FF D9) for ever.
cat > "$t/bin/rpicam-vid" <<'EOS'
#!/bin/sh
echo "$@" > "$(dirname "$0")/../args"
i=0
while :; do i=$(( (i + 1) % 10 )); printf '\377\330%s\377\331junk' "$i"; sleep 0.05; done
EOS
chmod +x "$t/bin/rpicam-vid"
PATH="$t/bin:$PATH" ROVER_CONF="$t/rover.conf" ./pi/rovercam 2>"$t/log" &
pid=$!
trap 'kill $pid 2>/dev/null; pkill -f "$t/bin/rpicam-vid" 2>/dev/null || true' EXIT
fail() { echo "FAIL: rovercam: $1"; exit 1; }
for i in 1 2 3 4 5 6 7 8 9 10; do curl -s -o /dev/null "http://127.0.0.1:$port/snap.jpg" && break; sleep 0.3; done
curl -s -D "$t/snap.head" -o "$t/snap.jpg" "http://127.0.0.1:$port/snap.jpg" || fail "no /snap.jpg"
[ "$(wc -c < "$t/snap.jpg" | tr -d ' ')" = 5 ] || fail "a snap is one whole frame"
[ "$(od -An -tx1 "$t/snap.jpg" | tr -d ' \n' | cut -c1-4)" = ffd8 ] || fail "a snap starts at the JPEG marker"
grep -qi '^X-Timestamp: [0-9]*\.[0-9]' "$t/snap.head" || fail "no X-Timestamp"
curl -s -m 1 -D "$t/stream.head" -o "$t/stream" "http://127.0.0.1:$port/mast.mjpg" || true
grep -qi 'multipart/x-mixed-replace; boundary=frame' "$t/stream.head" || fail "stream content type"
[ "$(grep -a -c '^--frame' "$t/stream")" -ge 5 ] || fail "fewer than 5 frames in a second"
grep -a -q '^Content-Length: 5' "$t/stream" || fail "part length"
grep -q -- '--rotation 180' "$t/args" && grep -q -- '--width 648 --height 486 --framerate 15' "$t/args" || fail "settings not passed to the camera"
ROVER_CONF="$t/rover.conf" ./pi/rover snap > "$t/rover-snap.jpg" || fail "rover snap through the stream"
[ "$(wc -c < "$t/rover-snap.jpg" | tr -d ' ')" = 5 ] || fail "rover snap is one frame from the stream"
echo "pi/rovercam: ok"
