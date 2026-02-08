# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

- **Purpose**: Options trading automation with Deribit
- **Hardware**: Intel 14900K CPU + NVIDIA 5090 Suprim SOC Liquid GPU
- **Python Version**: 3.13
- **Testing Framework**: pytest
- **OS**: Windows
- **IDE**: PyCharm 2025.2.3

## Setup

### Environment Variables

Database credentials are stored in `.env` file (not committed to git):

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set your database password:
   ```
   DB_PASSWORD=your_actual_password_here
   ```

The application will automatically load credentials from `.env` on startup.

## Permissions

**Granted**:
- Read (all files)
- Write (create, edit)
- Execute (bash, scripts)
- File management (move, rename)

**Requires Approval**:
- Delete operations (files, directories)

## Communication Style

- Always say the truth without sugar coating
- Mention potential problems and risks proactively
- Explain trade-offs clearly
- Don't use over-the-top validation or excessive praise
- Focus on technical accuracy over emotional validation
- If uncertain about something, investigate to find the truth first rather than confirming user's beliefs
- **BE PRECISE** - Never make assumptions or generalizations without verifying facts
- **NO HYPOCRISY** - If there's a problem, admit it directly and investigate thoroughly
- **NO QUICK FIXES** - Search deeply, think carefully, find the root cause, implement future-proof solutions
- When investigating issues:
  1. Don't assume - verify with actual data
  2. Don't quick patch - find the structural root cause
  3. Don't sugar coat - state the problem clearly
  4. Think through all possibilities before concluding
  5. Implement solutions that prevent the issue class, not just the symptom

## MANDATORY VERIFICATION CHECKLIST

**CRITICAL: Never say a task is "done" or "working" without completing ALL verification steps.**

For EVERY implementation task, you MUST:

1. ✅ **Code Implemented** - Write the code
2. ✅ **Code Runs Without Errors** - Execute and verify no crashes
3. ✅ **VERIFY ACTUAL RESULTS** - Check database/files/output contain expected data
4. ✅ **Compare Expected vs Actual** - Does the data match specifications?
5. ✅ **Test Edge Cases** - Handle failures, missing data, invalid inputs
6. ✅ **Show Verification Results** - Provide proof (query results, file contents, logs)
7. ✅ **Run System Validator** - Use `scripts/validate_system.py` to check all modules

**"Code runs without errors" ≠ "Code works correctly"**

### Examples of Proper Verification:

**BAD (No Verification):**
- "I implemented the data collector. It's running and logging successfully. ✓ Done!"

**GOOD (With Verification):**
- "I implemented the data collector. Let me verify:
  - Daemon started: ✓
  - Logs show collections: ✓
  - Database check: Last entry 2 minutes ago ✓
  - Expected tables populated: ✓
  - Sample query shows correct data: ✓
  Here are the verification results: [shows actual database query results]"

### When to Run Verification:

- **During Development**: Check incrementally as you build
- **After Implementation**: Before saying "done"
- **Before Committing**: Ensure everything actually works
- **After User Reports Issues**: Don't assume, verify with data

### Verification Tools:

- `python -m scripts.validate_system` - Comprehensive system health check (checks API, database, daemon, data freshness, quality)
- `python scripts/check_collection_status.py` - Check collection daemon and data recency
- `python scripts/check_database.py` - Database state verification
- Direct database queries - Check actual data
- Log file inspection - Verify operations completed
- API test calls - Ensure endpoints work

**REMEMBER: The user trusts you. Don't claim something works until you've PROVEN it works.**

## Documentation Rules

Only create detailed documentation summaries when:
1. The entire project phase is finished
2. The user explicitly confirms it's time to document
3. Requested by the user

For small task completions: write concise summaries in console output only. Do NOT create separate documentation files unless requested.

## Project Structure

Layered architecture: **Core** (definitions/models) → **Service** (orchestration) → **GUI/CLI** (presentation)

Key directories:
- `coding/core/` - Base classes, models, analytics
- `coding/service/` - High-level orchestration services
- `coding/gui/` - PySide6 GUI components
- `tests/` - Unit and integration tests
- `claude_resources/` - Claude reference documentation

**Full structure**: See `claude_resources/project_structure.md`

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

## Code Quality Checklist (MANDATORY)

**Before completing ANY code task, verify:**

1. **Layered Architecture**: Does the code follow Core → Service → GUI/CLI flow?
   - GUI/CLI should NEVER contain business logic or direct API calls
   - Services orchestrate operations using core components
   - Core contains definitions, models, and base methods

2. **Modularity**: Is each class/function doing ONE thing?
   - No monolithic classes with multiple responsibilities
   - Use strategy pattern for variations of same operation
   - Each capture/analysis type should be separate class, not if/elif chains

3. **Right Layer**: Is the code in the correct layer?
   - API calls → Service layer
   - Data models → Core layer
   - UI rendering → GUI layer
   - Business logic → Service layer (NOT GUI)

4. **No Shortcuts**: Even if it works, is it architecturally correct?
   - Quick solutions that violate architecture must be refactored
   - "It works" is not sufficient - it must be clean

**Example - WRONG (business logic in GUI):**
```python
# In GUI worker - BAD
for inst in instruments:
    ticker = service.get_ticker(instrument_name)  # API call in GUI!
    # ... process data
```

**Example - CORRECT (GUI calls service):**
```python
# In GUI worker - GOOD
result = capture_service.capture_gex_dex(currency, expiration)

# In service layer - business logic here
class DatabaseCaptureService:
    def capture_gex_dex(self, currency, expiration):
        # API calls and processing here
```

## Problem-Solving Approach

When fixing bugs or issues, follow structural thinking - not quick patches:

1. **Understand the flow first**: Before fixing, trace the data flow and understand WHY the problem exists
2. **Find the root cause**: Don't patch symptoms. If data is wrong, find where it becomes wrong in the pipeline
3. **Fix at the right layer**: The fix should be in the component responsible for that logic
4. **Maintain clean architecture**: Don't add external calls or workarounds that bypass the established flow

**Example - Wrong approach:**
```python
# Problem: OnChainAnalyzer has stale underlying_price
# Bad fix: Add separate API call in worker to fetch fresh price
perpetual_ticker = service.get_ticker(f"{currency}-PERPETUAL")
analyzer.underlying_price = perpetual_ticker.get("index_price")
```

**Example - Correct approach:**
```python
# Good fix: Fix the extraction logic inside OnChainAnalyzer
# The class receives the data, so it should extract the price correctly
def _extract_underlying_price(self, data):
    """Use mode (most common value) since stale instruments have old prices."""
    prices = [item.get("underlying_price") for item in data if item.get("underlying_price")]
    return Counter(prices).most_common(1)[0][0] if prices else 0.0
```

**Key principle**: If the same data source works correctly elsewhere (e.g., Snapshot tab), the problem is in how this component processes the data, not in the data itself.

**Data investigation example:**
When extracting a value from aggregated data (like `underlying_price` from book_summary), investigate the actual data distribution first:
```python
# Don't assume - investigate
from collections import Counter
prices = [item.get('underlying_price') for item in data]
print(Counter(prices).most_common(5))  # See what values exist

# Then find the pattern
# e.g., high-volume instruments have more recent data
active = [i for i in data if i.get('volume', 0) > 0]
highest_volume = max(active, key=lambda x: x.get('volume'))
# Use price from most active instrument
```

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
- Leading underscores allowed for internal/private methods (Python convention)

## Testing and Verification Methodology

**MANDATORY for all new strategy implementations and significant code changes.**

Core process:
1. Fetch real API data using existing services
2. Manually calculate expected values step-by-step
3. Compare implementation output to manual calculations
4. Test multiple scenarios (currencies, expirations, edge cases)
5. Document findings and fix issues before committing

Skip verification only for: trivial changes, pure refactoring with tests, GUI-only changes.

**Full methodology**: See `claude_resources/testing_guide.md`

## Quality Control Workflow

After the first major task is completed, it becomes the **reference example**. All future code must be validated against this reference using agents:

1. **Code Quality Agent** - Checks adherence to coding preferences
2. **Naming Agent** - Validates naming conventions are followed
3. **Flow Correctness Agent** - Ensures architecture patterns match reference

## Strategy System

Scores and ranks option strategies based on intrinsic metrics (risk/reward, cost efficiency, greeks) and on-chain metrics (max pain, GEX/DEX, OI, volume).

**Available Strategies**:
- Long Call/Put (single-leg)
- Bull Call Spread (multi-leg, Pydantic config, skew-aware optimization) - **GOLD STANDARD REFERENCE**

**Architecture**: Core (definitions, models, scoring) → Service (evaluation, finder) → GUI (strategy tab)

**Adding Strategies**: Inherit from BaseStrategy, register in factory, use Pydantic for config

**Quality Standards**: Bull Call Spread sets the bar (99/100 overall: 55 unit tests, 42 edge case tests, 100% critical path coverage, manual verification with real data)

**Full details**: See `claude_resources/strategy_system.md`
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
