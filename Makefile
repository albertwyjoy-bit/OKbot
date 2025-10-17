RUFF := $(shell command -v ruff 2> /dev/null || echo "uv run ruff")
PYRIGHT := $(shell command -v pyright 2> /dev/null || echo "uv run pyright")

.PHONY: prepare
prepare: download-deps

.PHONY: format
format:
	$(RUFF) check --fix
	$(RUFF) format

.PHONY: check
check:
	$(RUFF) check
	$(RUFF) format --check
	$(PYRIGHT)

.PHONY: test
test:
	uv run pytest tests -vv

.PHONY: build
build:
	uv run pyinstaller kimi.spec

include src/kimi_cli/deps/Makefile
