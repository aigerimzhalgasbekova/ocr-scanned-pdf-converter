# ocr-scanned-pdf-converter

OCR-based converter that turns scanned PTR-style (Periodic Disclosure of Financial Transactions) PDFs into structured Markdown.

## Requirements

System packages (OCR + PDF rendering):

```bash
# Ubuntu/Debian
sudo apt-get install poppler-utils tesseract-ocr

# macOS
brew install poppler tesseract
```

Python toolchain: [`uv`](https://docs.astral.sh/uv/) and Python 3.14.

## Setup

```bash
uv sync
uv run pre-commit install --hook-type commit-msg --hook-type pre-commit
```

## Run

```bash
uv run ocr-ptr-convert 9115728.pdf -o 9115728.md
```

Or via `main.py`:

```bash
uv run python main.py 9115728.pdf -o 9115728.md
```

## Develop

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src
```

## Notes
This script auto-tests 0/90/180/270 page rotation, uses OCR confidence plus table keywords to pick the best orientation, then assigns each X mark to the nearest transaction column or amount-code column before writing Markdown. For new forms, the main thing you may need to tune is the fallback column ratios near the top of the script, especially if the table layout differs from your attached PDF’s structure.
