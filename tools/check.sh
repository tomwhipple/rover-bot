#!/bin/sh
# Everything that can be checked off the robot, one step after another, each timed. This is the
# pre-push hook's work (`make check`). Every step runs even if an earlier one failed, so one push
# attempt shows all the problems; the exit status is 1 if any step failed.
#
#   ROVER_HOOK_SKIP=cppcheck,clang-tidy,mypy,arduino  skip those steps (printed loudly)
#   ROVER_CHECK_WRAP='cmd args'   run the whole check as `cmd args tools/check.sh` (a sandbox, a lock)
#   ROBO_CONTROL_DIR, PY          as in the Makefile; passed through
#
# POSIX sh, and no GNU-only options: it runs on macOS (bash 3.2, BSD tools) too.
set -u
# Tests must not write bytecode: sim/tests/__pycache__ is tracked, and a run would dirty it.
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

skip=",${ROVER_HOOK_SKIP:-},"
skipped=""
failed=""
t_all=$(date +%s)

if [ -n "${ROVER_HOOK_SKIP:-}" ]; then
  echo "######################################################################"
  echo "# ROVER_HOOK_SKIP=$ROVER_HOOK_SKIP -- these checks are NOT being run"
  echo "######################################################################"
fi

# step <name> <command...>: run, time and record one step.
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

# tool_step <skip-name> <tool> <install hint> <command...>: a required tool; a missing one fails
# the step (with the hint) unless the escape hatch names it.
tool_step() {
  sname=$1; tool=$2; hint=$3; shift 3
  case "$skip" in
    *",$sname,"*) printf '\n==> %s\n<== %s: SKIPPED by ROVER_HOOK_SKIP\n' "$sname" "$sname"
                  skipped="$skipped $sname"; return ;;
  esac
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf '\n==> %s\n<== %s: FAILED: %s not found. Install it: %s\n    (or set ROVER_HOOK_SKIP=%s for this push)\n' \
      "$sname" "$sname" "$tool" "$hint" "$sname"
    failed="$failed $sname"
    return
  fi
  step "$sname" "$@"
}

step "compile: pi/rover, pi/rovercam" make pi
step "compile: firmware core host library and test, roverd" make rover-core-lib firmware-host-build roverd

# The sketch needs arduino-cli and the AVR core; most machines have neither (it builds on the Pi).
case "$skip" in
  *,arduino,*) echo; echo "compile: Arduino sketch: SKIPPED by ROVER_HOOK_SKIP"; skipped="$skipped arduino" ;;
  *)
    if command -v arduino-cli >/dev/null 2>&1; then
      step "compile: Arduino sketch" make sketch-compile
    else
      echo; echo "compile: Arduino sketch: skipped: arduino-cli not installed"
      skipped="$skipped arduino"
    fi ;;
esac

step "test: make test" make test
step "test: make robo-test" make robo-test

tool_step cppcheck cppcheck "brew install cppcheck  (apt install cppcheck)" make cppcheck
tool_step clang-tidy "$CLANG_TIDY" "brew install llvm, then put \$(brew --prefix llvm)/bin on PATH  (apt install clang-tidy)" make tidy
tool_step mypy "${PY:-${ROBO_CONTROL_DIR:-../robo-control}/.venv/bin/python}" "set up the robo-control venv (README)" make mypy

printf '\n----------------------------------------------------------------------\n'
[ -z "$skipped" ] || echo "skipped:$skipped"
printf 'total: %ss\n' $(( $(date +%s) - t_all ))
if [ -n "$failed" ]; then
  echo "pre-push check FAILED:$failed"
  exit 1
fi
echo "pre-push check passed"
