##@ Housekeeping
.PHONY: help all clean

help: ## list the targets and variables
	@awk 'BEGIN {FS = ":.*## "} \
	  /^##@ / {printf "\n%s\n", substr($$0, 5); next} \
	  /^## / {printf "  %s\n", substr($$0, 4); next} \
	  /^[a-z][a-z0-9-]*:.*## / {printf "  make %-19s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

all: generated ## everything a fresh checkout needs before the tests or a run

clean: ## delete everything generated that git does not track
	rm -f pi/rover pi/rovercam
	rm -rf build
	find . -name __pycache__ -type d -not -path './.git/*' -not -path './.claude/*' -prune -exec rm -rf {} +
