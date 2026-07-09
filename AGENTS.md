# Repository Guidelines

## Project Structure & Module Organization

This is a Python SNS tech-briefing application. Runtime entrypoints live in `main.py` and `src/presentation/web/app.py`. Core code is layered under `src/`: `domain/` holds entities, protocols, services, and value objects; `application/use_cases/` coordinates collection, processing, and briefing workflows; `infrastructure/` contains collectors, AI, database, delivery, and configuration; `presentation/web/` contains FastAPI routes and templates. Tests are grouped under `tests/test_collectors/`, `tests/test_processing/`, and `tests/test_briefing/`. Static dashboard assets are in `docs/`, config is in `config/settings.yaml`, and local runtime output belongs in `data/` and `logs/`.

## Build, Test, and Development Commands

- `pip install -r requirements.txt`: install Python dependencies.
- `playwright install chromium`: install the browser runtime used by collectors.
- `python main.py serve`: run the web app and scheduler.
- `python main.py serve --no-scheduler`: run the web app without scheduled jobs.
- `python main.py collect-now` or `python main.py collect-now twitter`: trigger immediate collection.
- `pytest`: run the full test suite configured by `pyproject.toml`.
- `pytest tests/test_processing`: run a focused test package.

Before SNS collection, start Chrome with remote debugging on port `9222` and log into the required SNS accounts.

## Coding Style & Naming Conventions

Use Python 3.11+ and follow the layered architecture. Keep domain modules independent from infrastructure imports, define boundaries with `Protocol` interfaces, and wire implementations in `src/infrastructure/config/container.py`. Existing modules prefer snake_case file and function names, PascalCase classes, async I/O at external boundaries, and `from __future__ import annotations` in new modules. Keep comments and docstrings short and useful.

## Testing Guidelines

Use `pytest`; `asyncio_mode = "auto"` is already configured. Place new tests in the matching package under `tests/`, name files `test_*.py`, and name tests `test_<behavior>`. For collector or AI changes, prefer unit tests around parsing, filtering, and repository interactions before relying on live SNS or API calls.

## Commit & Pull Request Guidelines

Recent commits use Conventional Commit-style prefixes such as `feat(...)`, `fix(...)`, and `perf(...)`, often with a short Korean summary. Keep commit subjects imperative and scoped, for example `fix(briefing): preserve generated date`. Pull requests should include a concise description, linked context, test results, and screenshots when dashboard or HTML output changes. Note any required `.env`, Firebase, Chrome CDP, or `config/settings.yaml` changes.

## Security & Configuration Tips

Do not commit secrets. Keep API keys, SMTP credentials, SNS credentials, and Firebase paths in `.env` or local-only files. Treat service-account JSON files and `data/*.db*` as sensitive local runtime artifacts unless explicitly intended for sharing.
