# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

- **Purpose**: Options trading automation with Deribit
- **Hardware**: Intel 14900K CPU + NVIDIA 5090 Suprim SOC Liquid GPU
- **Python Version**: 3.13
- **Testing Framework**: pytest

## Permissions

Full permissions for read, write, execute, and file management. Only removal operations require user approval.

## Documentation Rules

Only create detailed documentation summaries when:
1. The entire project phase is finished
2. The user explicitly confirms it's time to document
3. Requested by the user

For small task completions: write concise summaries in console output only. Do NOT create separate documentation files unless requested.

## Project Structure

```
option_trading/
├── coding/
│   ├── core/              # Definitions, models, base classes
│   │   ├── api/           # API connection, parsing, validation
│   │   ├── endpoints/     # API endpoint definitions
│   │   ├── logging/       # Logging configuration
│   │   └── schemas/       # Response schemas for validation
│   └── service/           # High-level orchestration services
│       └── deribit/       # Deribit API service
├── tests/
│   ├── unit/              # Unit tests
│   └── integration/       # Integration tests
├── output/
│   ├── data/              # CSV exports and data files
│   └── log/               # Log files with timestamps
```

**Structure Rule**: Code files must be inside related folders (e.g., `core/logging/logging_setup.py` not `core/logging_setup.py`).

## Git Workflow

- **Repository**: https://github.com/nick9248/rsc_option_trading.git
- **Main branch**: `main`

For each task:
1. Create a new branch from main
2. Implement the task
3. Wait for user confirmation
4. Push and merge to main

## Architecture Principles

Layered architecture with clear separation:

```
Core (definitions/models)
    ↓
Base Methods (connect, fetch, parse, check)
    ↓
Services (high-level orchestration using base methods)
```

Example: For API fetching, have core definitions, then base methods (connect, fetch, parse, check), then services that orchestrate these methods.

## Coding Preferences

- Simple and clear without complexity unless truly needed
- Scalable for future expansion (e.g., Asset class with expandable attributes)
- Completely modular
- Clear, understandable docstrings

## Logging System

Use `logging` module. Never use `print()`.

```python
# At the top of every Python file:
import logging

# For standalone scripts/pipelines:
from coding.core.logging.logging_setup import init_logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)

# For services/modules (logging already initialized):
logger = logging.getLogger(__name__)

# Usage:
logger.info("Starting capture...")
logger.warning("Connection timeout")
logger.error(f"Failed: {error}")
logger.debug("Detailed debug info")
```

## Naming Conventions

- Descriptive names related to the method's purpose
- No abbreviations
- No leading underscores

## Quality Control Workflow

After the first major task is completed, it becomes the **reference example**. All future code must be validated against this reference using agents:

1. **Code Quality Agent** - Checks adherence to coding preferences
2. **Naming Agent** - Validates naming conventions are followed
3. **Flow Correctness Agent** - Ensures architecture patterns match reference

## Commands

```bash
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/

# Run a specific test
pytest tests/unit/test_file.py::test_function_name

# Run with verbose output
pytest -v
```
