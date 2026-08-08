"""
Repo-wide GUI-layering regression guard (Wave G task G2-F fix 3).

This repo's CLAUDE.md Code Quality Checklist: "GUI/CLI never contains
business logic or direct API calls; services orchestrate using base
methods." coding/gui/tabs/database_tab.py's SyncWorker used to violate this
by importing psycopg2 and opening raw DB connections directly inside a
QThread in the GUI layer. That logic moved to
coding.service.database.vps_sync_service.VpsSyncService (see
tests/unit/gui/test_database_tab.py for the SyncWorker-specific proof).

This test is the general-purpose guard: it walks every .py file under
coding/gui/ (not just database_tab.py) and asserts none of them import
psycopg2, by parsing the AST rather than grepping so both `import
psycopg2` and `from psycopg2 import ...`, anywhere in the file (module- or
function-level), are caught.
"""
import ast
from pathlib import Path

GUI_ROOT = Path(__file__).parents[3] / "coding" / "gui"


def _gui_python_files():
    return sorted(GUI_ROOT.rglob("*.py"))


def _imports_psycopg2(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "psycopg2" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "psycopg2":
                return True
    return False


class TestGuiLayerNeverImportsPsycopg2:
    def test_gui_root_exists_and_is_non_empty(self):
        """Sanity check the walk is actually scanning real files, not an empty/wrong path."""
        files = _gui_python_files()
        assert len(files) > 5, f"Expected several .py files under {GUI_ROOT}, found {len(files)}"

    def test_no_gui_file_imports_psycopg2_directly(self):
        offenders = [str(f.relative_to(GUI_ROOT)) for f in _gui_python_files() if _imports_psycopg2(f)]
        assert offenders == [], (
            f"GUI layer imports psycopg2 directly (business logic/DB access belongs "
            f"in coding/service/, per CLAUDE.md's Code Quality Checklist): {offenders}"
        )
