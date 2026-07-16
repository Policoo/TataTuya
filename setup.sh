#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"

if [[ ! -d "$VENV_DIR" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Error: python3 or python is required to create the virtual environment." >&2
    exit 1
  fi

  echo "Creating virtual environment at ${VENV_DIR}"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="${VENV_DIR}/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Error: ${VENV_DIR} exists, but ${VENV_PYTHON} was not found or is not executable." >&2
  exit 1
fi

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  echo "Error: requirements.txt was not found at ${REQUIREMENTS_FILE}" >&2
  exit 1
fi

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r "$REQUIREMENTS_FILE"

echo "Setup complete. Use: source .venv/bin/activate"
