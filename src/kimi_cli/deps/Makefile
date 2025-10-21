THIS_DIR := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))
BIN_DIR := $(THIS_DIR)/bin
TMP_DIR := $(THIS_DIR)/tmp

# Allow override via environment: RG_VERSION=15.0.0 make download-ripgrep
RG_VERSION ?= 15.0.0
OS := $(shell uname -s)
ARCH := $(shell uname -m)

# Map OS/ARCH to ripgrep TARGET name
# See: https://github.com/BurntSushi/ripgrep/releases
ifeq ($(OS),Darwin)
  ifeq ($(ARCH),arm64)
    RG_TARGET := aarch64-apple-darwin
  else ifeq ($(ARCH),x86_64)
    RG_TARGET := x86_64-apple-darwin
  else
    $(error Unsupported macOS architecture: $(ARCH))
  endif
else ifeq ($(OS),Linux)
  ifeq ($(ARCH),x86_64)
    RG_TARGET := x86_64-unknown-linux-musl
  else ifeq ($(ARCH),aarch64)
    RG_TARGET := aarch64-unknown-linux-gnu
  else ifeq ($(ARCH),armv7l)
    RG_TARGET := arm-unknown-linux-gnueabihf
  else
    $(error Unsupported Linux architecture: $(ARCH))
  endif
else
  $(error Unsupported OS: $(OS))
endif

RG_URL := https://github.com/BurntSushi/ripgrep/releases/download/$(RG_VERSION)/ripgrep-$(RG_VERSION)-$(RG_TARGET).tar.gz
RG_TAR := ripgrep-$(RG_VERSION)-$(RG_TARGET).tar.gz


.PHONY: download-ripgrep
download-ripgrep:
	@if [ -f "$(BIN_DIR)/rg" ]; then \
		echo "rg already installed at $(BIN_DIR)/rg"; \
	else \
		echo "Downloading ripgrep $(RG_VERSION) from: $(RG_URL)"; \
		mkdir -p "$(BIN_DIR)" "$(TMP_DIR)"; \
		if command -v curl >/dev/null 2>&1; then \
			curl -L --fail -o "$(TMP_DIR)/$(RG_TAR)" "$(RG_URL)"; \
		else \
			if command -v wget >/dev/null 2>&1; then \
				wget -O "$(TMP_DIR)/$(RG_TAR)" "$(RG_URL)"; \
			else \
				echo "Error: neither curl nor wget is available" && exit 1; \
			fi; \
		fi; \
		tar -xzf "$(TMP_DIR)/$(RG_TAR)" -C "$(TMP_DIR)"; \
		cp "$(TMP_DIR)/ripgrep-$(RG_VERSION)-$(RG_TARGET)/rg" "$(BIN_DIR)/rg"; \
		chmod +x "$(BIN_DIR)/rg"; \
		echo "rg installed at $(BIN_DIR)/rg"; \
	fi


.PHONY: download-deps
download-deps: download-ripgrep
