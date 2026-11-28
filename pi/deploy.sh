#!/bin/sh
# The Pi's half of `make deploy`, run there over ssh (`sh -s < pi/deploy.sh`) once the sources are in
# ~/$PI_DIR and robo-control's core/ and schemas/ in ~/$RC_DIR.
#
# Builds pi/rover, pi/rovercam and roverd where their sources changed (roverd with CMake, Release);
# installs each program, unit file and the deployed record only where it differs from what is
# installed; restarts a service only when its program, unit or record changed. A failed build stops
# here and leaves the installed programs as they were.
#
# Environment: PI_DIR, RC_DIR (under ~), PREFIX, CONF, CXXFLAGS, ROVER_COMMIT, ROVER_DIRTY,
# RC_COMMIT, RC_DIRTY (0 or 1).
set -eu
cd "$HOME"

src=$PI_DIR/pi
build=$PI_DIR/build/roverd
share=$PREFIX/share/rover
units=/etc/systemd/system
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

missing=""
for p in cmake g++ libprotobuf-dev protobuf-compiler libzenohc-dev libzenohcpp-dev; do
  dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"
done
if [ -n "$missing" ]; then
  echo "deploy: the Pi lacks$missing (zenoh-c and zenoh-cpp: the Debian packages of the version robo-control uses)" >&2
  exit 1
fi

# stale OUT IN...: OUT is missing or older than one of IN. The sources arrive by checksum, so a
# changed file is newer than the last build.
stale() {
  out=$1; shift
  [ ! -e "$out" ] || [ -n "$(find "$@" -newer "$out")" ]
}

if stale "$src/rover" "$src/rover.cpp" "$src/rover_conf.hpp"; then
  echo "deploy: building rover"
  # shellcheck disable=SC2086
  g++ $CXXFLAGS -o "$src/rover.new" "$src/rover.cpp"
  mv "$src/rover.new" "$src/rover"
fi
if stale "$src/rovercam" "$src/rovercam.cpp" "$src/rover_conf.hpp"; then
  echo "deploy: building rovercam"
  # shellcheck disable=SC2086
  g++ $CXXFLAGS -pthread -o "$src/rovercam.new" "$src/rovercam.cpp"
  mv "$src/rovercam.new" "$src/rovercam"
fi
# CMake decides what is stale. ROBO_WERROR off: a newer compiler must not break a deploy over a warning.
cmake -S "$src/roverd" -B "$build" -DCMAKE_BUILD_TYPE=Release -DROBO_CONTROL_DIR="$HOME/$RC_DIR" \
  -DROBO_WERROR=OFF >/dev/null
cmake --build "$build" --target roverd --parallel "$(nproc)"
test -x "$build/roverd"
"$build/roverd" --version >/dev/null

# put SRC DST MODE: install SRC as DST unless identical; true if it installed. Called as a
# condition, where set -e does not reach: a failed install exits explicitly.
put() {
  if cmp -s "$1" "$2"; then return 1; fi
  sudo install -D -m "$3" "$1" "$2" || exit 1
  echo "deploy: installed $2"
}

roverd_changed=0
rovercam_changed=0
units_changed=0
put "$src/rover" "$PREFIX/bin/rover" 755 || true
if put "$src/rovercam" "$PREFIX/bin/rovercam" 755; then rovercam_changed=1; fi
if put "$build/roverd" "$PREFIX/bin/roverd" 755; then roverd_changed=1; fi
if put "$src/roverd.service" "$units/roverd.service" 644; then roverd_changed=1; units_changed=1; fi
if put "$src/rovercam.service" "$units/rovercam.service" 644; then rovercam_changed=1; units_changed=1; fi
test -e "$CONF" || sudo install -m 644 "$src/rover.conf" "$CONF"

# The record. description_commit is the short hash, as a simulated run's; "-dirty" when the deployed
# sources differed from it.
desc=$(printf '%s' "$ROVER_COMMIT" | cut -c1-7)
[ "$ROVER_DIRTY" = 1 ] && desc="$desc-dirty"
printf 'ROVER_DESCRIPTION_COMMIT=%s\n' "$desc" > "$tmp/deployed.env"
if put "$tmp/deployed.env" "$share/deployed.env" 644; then roverd_changed=1; fi
if [ "$roverd_changed" = 1 ] || [ "$rovercam_changed" = 1 ] || [ ! -e "$share/deployed.json" ]; then
  tf() { [ "$1" = 1 ] && echo true || echo false; }
  cat > "$tmp/deployed.json" <<EOF
{
  "rover_commit": "$ROVER_COMMIT",
  "rover_dirty": $(tf "$ROVER_DIRTY"),
  "robo_control_commit": "$RC_COMMIT",
  "robo_control_dirty": $(tf "$RC_DIRTY"),
  "description_commit": "$desc",
  "roverd": "$("$PREFIX/bin/roverd" --version)",
  "roverd_sha256": "$(sha256sum "$PREFIX/bin/roverd" | cut -d' ' -f1)",
  "rovercam_sha256": "$(sha256sum "$PREFIX/bin/rovercam" | cut -d' ' -f1)",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  put "$tmp/deployed.json" "$share/deployed.json" 644 || true
fi

if [ "$units_changed" = 1 ]; then sudo systemctl daemon-reload; fi
for s in roverd rovercam; do
  sudo systemctl enable --quiet "$s"
  eval changed=\$${s}_changed
  if [ "$changed" = 1 ]; then
    echo "deploy: restarting $s"
    sudo systemctl restart "$s"
  elif ! systemctl is-active --quiet "$s"; then
    echo "deploy: starting $s"
    sudo systemctl start "$s"
  else
    echo "deploy: $s unchanged, left running"
  fi
done
sleep 2
for s in roverd rovercam; do
  systemctl is-active --quiet "$s" || { echo "deploy: $s is not running" >&2; systemctl status --no-pager "$s" >&2; exit 1; }
done
echo "deploy: $("$PREFIX/bin/roverd" --version), description commit $desc"
