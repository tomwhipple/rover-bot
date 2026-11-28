#!/bin/sh
# Everything that can be checked off the robot: the pre-push hook's work (`make prepush`).
#
# First, one after another and alone, what every gate reads: `make generated` (the Pi programs, the
# firmware core library, roverd and its unit-test binary) and the firmware core's host test binary.
# Then the gates, which share nothing, at the same time: host, sim-test, roverd-test, pi-test,
# cppcheck, tidy, mypy, and sketch-compile where arduino-cli is installed. Each gate is `make <gate>`
# with its output in its own log, build/prepush-logs/<stamp>-<pid>/<gate>.log; a one-line PASS or
# FAIL per gate says how long it took, and a failing gate prints `FAIL: <gate>` with the tail of its
# log and its path. Every gate runs whatever another did; exit status 1 if any failed.
#
#   PREPUSH_SERIAL=1                                  run the gates one at a time
#   ROVER_HOOK_SKIP=cppcheck,clang-tidy,mypy,arduino  skip those steps (printed loudly)
#   ROVER_CHECK_WRAP='cmd args'   run the whole check as `cmd args tools/prepush.sh` (a sandbox, a lock)
#   ROBO_CONTROL_DIR, PY, PYTEST_WORKERS   as in the Makefile; passed through
#
# POSIX sh, and no GNU-only options: it runs on macOS (bash 3.2, BSD tools) too.
set -u
# Tests must not write bytecode.
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

cd "$(dirname "$0")/.."
for v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done

if [ -n "${ROVER_CHECK_WRAP:-}" ] && [ -z "${ROVER_CHECK_WRAPPED:-}" ]; then
  ROVER_CHECK_WRAPPED=1
  export ROVER_CHECK_WRAPPED
  # shellcheck disable=SC2086
  exec $ROVER_CHECK_WRAP "$0" "$@"
fi

# Homebrew's llvm is keg-only: clang-tidy is not on PATH after `brew install llvm`.
if [ -z "${CLANG_TIDY:-}" ]; then
  CLANG_TIDY=clang-tidy
  for d in /opt/homebrew/opt/llvm/bin /usr/local/opt/llvm/bin; do
    if ! command -v clang-tidy >/dev/null 2>&1 && [ -x "$d/clang-tidy" ]; then CLANG_TIDY=$d/clang-tidy; fi
  done
fi
export CLANG_TIDY

MAKE_CMD=${MAKE:-make}
skip=",${ROVER_HOOK_SKIP:-},"
skipped=""
failed=""
t_all=$(date +%s)
dir=build/prepush-logs/$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$dir"

if [ -n "${ROVER_HOOK_SKIP:-}" ]; then
  echo "######################################################################"
  echo "# ROVER_HOOK_SKIP=$ROVER_HOOK_SKIP -- these checks are NOT being run"
  echo "######################################################################"
fi

# step <name> <command...>: run one serial step, timed.
step() {
  name=$1; shift
  printf '\n==> %s\n' "$name"
  t0=$(date +%s)
  if "$@"; then rc=0; else rc=$?; fi
  t1=$(date +%s)
  if [ "$rc" -eq 0 ]; then
    printf '<== %s: ok (%ss)\n' "$name" $((t1 - t0))
  else
    printf '<== %s: FAILED (%ss)\n' "$name" $((t1 - t0))
    failed="$failed $name"
  fi
}

step "generated: pi/rover, pi/rovercam, firmware core host library, roverd" $MAKE_CMD generated
step "compile: firmware core host test" $MAKE_CMD firmware-host-build

if [ -n "$failed" ]; then
  echo "pre-push check FAILED:$failed (no gate ran)"
  exit 1
fi

# --- the gates ---------------------------------------------------------------------------------
gates=""

# gate <make target> [<skip-name> <tool> <install hint>]: queue a gate; a missing tool fails it
# (with the hint) unless the escape hatch names it.
gate() {
  target=$1
  if [ "$#" -ge 4 ]; then
    sname=$2; tool=$3; hint=$4
    case "$skip" in
      *",$sname,"*) printf '\n%s: SKIPPED by ROVER_HOOK_SKIP\n' "$sname"; skipped="$skipped $sname"; return ;;
    esac
    if ! command -v "$tool" >/dev/null 2>&1; then
      printf '\nFAIL: %s: %s not found. Install it: %s\n      (or set ROVER_HOOK_SKIP=%s for this push)\n' \
        "$sname" "$tool" "$hint" "$sname"
      failed="$failed $sname"
      return
    fi
  fi
  gates="$gates $target"
}

run_gate() {   # leaves <gate>.log, <gate>.secs and (last) <gate>.rc
  t0=$(date +%s)
  $MAKE_CMD "$1" > "$dir/$1.log" 2>&1
  r=$?
  echo $(( $(date +%s) - t0 )) > "$dir/$1.secs"
  echo $r > "$dir/$1.rc.tmp"
  mv "$dir/$1.rc.tmp" "$dir/$1.rc"
}

# The sketch needs arduino-cli and the AVR core; most machines have neither (it builds on the Pi).
case "$skip" in
  *,arduino,*) echo; echo "sketch-compile: SKIPPED by ROVER_HOOK_SKIP"; skipped="$skipped arduino" ;;
  *)
    if command -v arduino-cli >/dev/null 2>&1; then gates="$gates sketch-compile"
    else echo; echo "sketch-compile: skipped: arduino-cli not installed"; skipped="$skipped arduino"; fi ;;
esac

gate host
gate sim-test
gate roverd-test
gate pi-test
gate cppcheck cppcheck cppcheck "brew install cppcheck  (apt install cppcheck)"
gate tidy clang-tidy "$CLANG_TIDY" "brew install llvm, then put \$(brew --prefix llvm)/bin on PATH  (apt install clang-tidy)"
gate mypy mypy "${PY:-${ROBO_CONTROL_DIR:-../robo-control}/.venv/bin/python}" "set up the robo-control venv (README), then pip install mypy"

printf '\n== gates:%s%s ==\n' "$gates" "$([ "${PREPUSH_SERIAL:-0}" != 0 ] && echo ' (one at a time)')"
trap 'kill $(jobs -p) 2>/dev/null; exit 130' INT TERM
if [ "${PREPUSH_SERIAL:-0}" != 0 ]; then
  for g in $gates; do run_gate "$g"; done
else
  for g in $gates; do run_gate "$g" & done
  wait
fi
for g in $gates; do
  if [ "$(cat "$dir/$g.rc")" = 0 ]; then
    printf 'PASS  %-14s %4ss\n' "$g" "$(cat "$dir/$g.secs")"
  else
    printf 'FAIL: %s (%ss) -- its log: %s/%s.log\n' "$g" "$(cat "$dir/$g.secs")" "$dir" "$g"
    tail -n 25 "$dir/$g.log" | sed 's/^/    | /'
    failed="$failed $g"
  fi
done

printf '\n----------------------------------------------------------------------\n'
[ -z "$skipped" ] || echo "skipped:$skipped"
printf 'total: %ss; logs in %s\n' $(( $(date +%s) - t_all )) "$dir"
if [ -n "$failed" ]; then
  echo "pre-push check FAILED:$failed"
  exit 1
fi
echo "pre-push check passed"
