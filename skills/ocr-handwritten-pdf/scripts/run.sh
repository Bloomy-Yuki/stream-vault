#!/bin/sh
# Entry point for the ocr-handwritten-pdf skill.
#
# Usage:
#   run.sh ocr    --pdf <file.pdf> --out <dir> [more ocr_hybrid.py flags]
#   run.sh format --out <dir> [more format_md.py flags]
#
# Loads OPENROUTER_API_KEY from the environment or from
# $OCR_ENV_FILE (default: ~/.config/ocr-notes/.env) and runs the Python
# script with its dependencies, preferring `uv` so no venv has to be managed.
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=${OCR_ENV_FILE:-$HOME/.config/ocr-notes/.env}

if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "OPENROUTER_API_KEY is not set." >&2
    echo "Put 'OPENROUTER_API_KEY=sk-or-...' in $ENV_FILE (chmod 600) or export it." >&2
    exit 2
fi

case "${1:-}" in
    ocr | hybrid) shift; script="$DIR/ocr_hybrid.py" ;;
    format | fmt) shift; script="$DIR/format_md.py" ;;
    *)
        echo "usage: run.sh {ocr|format} [options]" >&2
        exit 64
        ;;
esac

if command -v uv >/dev/null 2>&1; then
    exec uv run --quiet --no-project --python 3.12 \
        --with 'pymupdf>=1.24' --with 'pillow>=10' --with 'tqdm>=4.66' \
        python "$script" "$@"
fi

exec python3 "$script" "$@"
