#!/usr/bin/env bash
set -euo pipefail

# TurboFind Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/jcmaslan/TurboFind/main/scripts/install.sh | bash

TARBALL="https://github.com/jcmaslan/TurboFind/archive/refs/heads/main.tar.gz"
INSTALL_DIR="${TURBOFIND_HOME:-$HOME/.turbofind}"

echo "Installing TurboFind..."

# Download and extract (clean install to avoid stale files)
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
curl -fsSL "$TARBALL" | tar xz --strip-components=1 -C "$INSTALL_DIR"

# Install Python package
python3 -m pip install -e "$INSTALL_DIR" --quiet

# Pull embedding model if Ollama is available
if command -v ollama &>/dev/null; then
  if ! ollama pull nomic-embed-text; then
    echo "⚠ Failed to pull nomic-embed-text. Ensure Ollama is running, then run: ollama pull nomic-embed-text"
  fi
else
  echo "⚠ Ollama not found. Install it from https://ollama.com then run: ollama pull nomic-embed-text"
fi

echo ""
echo "TurboFind installed. Available commands: tf-init, tf-search, tf-upsert"
echo ""
echo "Next steps:"
echo "  1. Set your API key:  export ANTHROPIC_API_KEY=\"your-key-here\""
echo "  2. In your project:   tf-init && tf-upsert ."
