"""Unit tests for DaemonServiceCheck."""

import subprocess

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.daemon_checker import DaemonServiceCheck


def _fake_runner(stdout):
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    return runner


def _raising_runner(exc):
    def runner(*args, **kwargs):
        raise exc
    return runner


def test_active_service_passes():
    check = DaemonServiceCheck(subprocess_runner=_fake_runner("active\n"))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.PASS


def test_inactive_service_fails():
    check = DaemonServiceCheck(subprocess_runner=_fake_runner("inactive\n"))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL


def test_missing_systemctl_warns():
    check = DaemonServiceCheck(subprocess_runner=_raising_runner(FileNotFoundError()))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.WARN
