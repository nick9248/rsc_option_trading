"""
Logging configuration for the options trading project.

Provides centralized logging setup with console and file output.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def get_project_root() -> Path:
    """
    Get the project root directory.

    Returns:
        Path to project root directory.
    """
    return Path(__file__).parent.parent.parent.parent


def get_log_directory() -> Path:
    """
    Get the log output directory.

    Returns:
        Path to output/log directory.
    """
    return get_project_root() / "output" / "log"


def generate_log_filename(task_name: str) -> str:
    """
    Generate a log filename with task name and timestamp.

    Args:
        task_name: Name of the task or process being logged.

    Returns:
        Formatted log filename with timestamp.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{task_name}_{timestamp}.log"


def init_logging(
    level: str = "INFO",
    task_name: Optional[str] = None,
    log_to_file: bool = True,
    log_format: Optional[str] = None
) -> None:
    """
    Initialize logging configuration for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        task_name: Name of the task for log file naming. If None and log_to_file is True,
                   uses 'general' as the task name.
        log_to_file: Whether to write logs to file in output/log directory.
        log_format: Optional custom log format string.
    """
    if log_format is None:
        log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handlers = []

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(logging.Formatter(log_format))
    handlers.append(console_handler)

    if log_to_file:
        if task_name is None:
            task_name = "general"
        log_directory = get_log_directory()
        log_directory.mkdir(parents=True, exist_ok=True)
        log_filename = generate_log_filename(task_name)
        log_path = log_directory / log_filename

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(logging.Formatter(log_format))
        handlers.append(file_handler)

    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        handlers=handlers,
        force=True
    )

    logging.getLogger(__name__).debug(f"Logging initialized at {level} level")
