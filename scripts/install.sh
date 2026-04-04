#!/usr/bin/env bash
set -euo pipefail

# TurboFind Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/jcmaslan/TurboFind/main/scripts/install.sh | bash

REPO="https://github.com/jcmaslan/TurboFind.git"
INSTALL_DIR="${TURBOFIND_HOME:-$HOME/.turbofind}"

echo "Installing TurboFind..."

# Clone or update
if [ -d "$INSTALL_DIR" ]; then
  echo "Updating existing installation at $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO" "$INSTALL_DIR"
fi

# Install Python package
pip install -e "$INSTALL_DIR" --quiet

# Pull embedding model if Ollama is available
if command -v ollama &>/dev/null; then
  echo "Pulling nomic-embed-text model..."
  ollama pull nomic-embed-text
else
  echo "⚠ Ollama not found. Install it from https://ollama.com then run: ollama pull nomic-embed-text"
fi

echo ""
echo "TurboFind installed. Available commands: tf-init, tf-search, tf-upsert"
echo ""
echo "Next steps:"
echo "  1. Set your API key:  export ANTHROPIC_API_KEY=\"your-key-here\""
echo "  2. In your project:   tf-init && tf-upsert ."
