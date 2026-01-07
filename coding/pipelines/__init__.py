"""
Pipelines module for orchestrating data fetching workflows.

Contains pipeline functions that coordinate services to accomplish tasks.
"""

from coding.pipelines.fetch_and_process import fetch_and_process

__all__ = ["fetch_and_process"]
