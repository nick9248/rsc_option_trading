"""
Tests for VpsCodeStagingService: command order, refusals and the safety guard.

A fake runner records every command and answers like git/ssh/scp would. No real
git, ssh or scp process is started.
"""
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pytest

from coding.service.deployment.vps_code_staging_service import (
    FORBIDDEN_REMOTE_TOKENS,
    RESTART_REMINDER,
    CommandResult,
    ForbiddenCommandError,
    StagingError,
    VpsCodeStagingService,
    guard_command,
)
from scripts import deploy_to_vps

OLD_HEAD = "a" * 40
TARGET = "b" * 40
NEWER_ORIGIN_MAIN = "c" * 40
LOCAL_REPOSITORY = Path("local_repository")
REPOSITORY = str(LOCAL_REPOSITORY)
FIXED_NOW = datetime(2026, 11, 10, 18, 30, tzinfo=timezone.utc)
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "option-server"]
SMOKE_COMMAND = (
    "cd /home/nick/option_trading && /home/nick/option_trading/.venv/bin/python -c "
    "'import logging; logging.disable(logging.CRITICAL); "
    "from coding.service.data_collection.unified_scheduler import UnifiedScheduler; "
    "print(\"imports OK\")'"
)


@dataclass
class RecordedCall:
    arguments: List[str]
    timeout_seconds: float
    working_directory: Optional[Path]


@dataclass
class FakeRunner:
    """Answers commands from a small model of the local repository and the VPS."""

    remote_head: str = OLD_HEAD
    origin_main: str = TARGET
    known_local: Set[str] = field(default_factory=lambda: {OLD_HEAD, TARGET, NEWER_ORIGIN_MAIN})
    ancestors: Set[Tuple[str, str]] = field(
        default_factory=lambda: {
            (OLD_HEAD, TARGET), (OLD_HEAD, NEWER_ORIGIN_MAIN), (TARGET, NEWER_ORIGIN_MAIN)
        }
    )
    remote_status: str = ""
    remote_branch: str = "main"
    backups_listing: str = "deploy_20261012\n"
    smoke_stdout: str = "imports OK\n"
    smoke_return_code: int = 0
    calls: List[RecordedCall] = field(default_factory=list)

    def run(
        self,
        arguments: Sequence[str],
        timeout_seconds: float,
        working_directory: Optional[Path] = None,
    ) -> CommandResult:
        assert timeout_seconds > 0
        self.calls.append(RecordedCall(list(arguments), timeout_seconds, working_directory))
        program = arguments[0]
        if program == "git":
            assert list(arguments[:3]) == ["git", "-C", REPOSITORY]
            return self._git(list(arguments[3:]))
        if program == "ssh":
            assert list(arguments[:-1]) == SSH
            return self._ssh(arguments[-1])
        if program == "scp":
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected program {program}")

    def _git(self, git_arguments: List[str]) -> CommandResult:
        if git_arguments[:3] == ["rev-parse", "--verify", "--quiet"]:
            revision = git_arguments[3].removesuffix("^{commit}")
            if revision in ("refs/remotes/origin/main", "origin/main"):
                return CommandResult(0, self.origin_main + "\n", "")
            for commit in self.known_local:
                if commit.startswith(revision):
                    return CommandResult(0, commit + "\n", "")
            return CommandResult(1, "", "")
        if git_arguments[:2] == ["merge-base", "--is-ancestor"]:
            ancestor, descendant = git_arguments[2], git_arguments[3]
            is_ancestor = ancestor == descendant or (ancestor, descendant) in self.ancestors
            return CommandResult(0 if is_ancestor else 1, "", "")
        if git_arguments[0] in ("tag", "bundle"):
            return CommandResult(0, "", "")
        if git_arguments[:2] == ["diff", "--shortstat"]:
            return CommandResult(0, " 3 files changed, 10 insertions(+), 2 deletions(-)\n", "")
        raise AssertionError(f"unexpected git command {git_arguments}")

    def _ssh(self, remote_command: str) -> CommandResult:
        if remote_command.endswith("rev-parse HEAD"):
            return CommandResult(0, self.remote_head + "\n", "")
        if remote_command.endswith("symbolic-ref --short HEAD"):
            return CommandResult(0, self.remote_branch + "\n", "")
        if remote_command.endswith("status --porcelain"):
            return CommandResult(0, self.remote_status, "")
        if "ls -1" in remote_command:
            return CommandResult(0, self.backups_listing, "")
        if remote_command.startswith("mkdir ") or " bundle verify " in remote_command:
            return CommandResult(0, "", "")
        if " fetch --no-tags " in remote_command:
            return CommandResult(0, "", "")
        if remote_command.endswith("merge --ff-only incoming-main"):
            self.remote_head = TARGET
            return CommandResult(0, "", "")
        if remote_command == SMOKE_COMMAND:
            return CommandResult(self.smoke_return_code, self.smoke_stdout, "boom")
        raise AssertionError(f"unexpected remote command {remote_command}")

    def programs(self) -> List[str]:
        return [call.arguments[0] for call in self.calls]

    def remote_commands(self) -> List[str]:
        return [call.arguments[-1] for call in self.calls if call.arguments[0] == "ssh"]


def _service(runner: FakeRunner) -> VpsCodeStagingService:
    return VpsCodeStagingService(
        runner=runner, local_repository=LOCAL_REPOSITORY, clock=lambda: FIXED_NOW
    )


def _git(*git_arguments: str) -> List[str]:
    return ["git", "-C", REPOSITORY, *git_arguments]


def _bundle_path(runner: FakeRunner) -> str:
    for call in runner.calls:
        if call.arguments[3:5] == ["bundle", "create"]:
            return call.arguments[5]
    raise AssertionError("no bundle was created")


def test_happy_path_issues_the_expected_commands_in_order(caplog):
    runner = FakeRunner()
    with caplog.at_level(logging.INFO):
        result = _service(runner).stage(TARGET[:7])

    bundle_path = _bundle_path(runner)
    remote_bundle = "/home/nick/backups/deploy_20261110/rsc_main_bbbbbbbbbbbb.bundle"
    expected = [
        _git("rev-parse", "--verify", "--quiet", "bbbbbbb^{commit}"),
        _git("rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}"),
        _git("merge-base", "--is-ancestor", TARGET, TARGET),
        SSH + ["git -C /home/nick/option_trading rev-parse HEAD"],
        SSH + ["git -C /home/nick/option_trading symbolic-ref --short HEAD"],
        SSH + ["git -C /home/nick/option_trading status --porcelain"],
        _git("rev-parse", "--verify", "--quiet", OLD_HEAD + "^{commit}"),
        _git("merge-base", "--is-ancestor", OLD_HEAD, TARGET),
        # The target is the origin/main tip, so that ref names the bundle content.
        _git("bundle", "create", bundle_path, "refs/remotes/origin/main", "^" + OLD_HEAD),
        _git("bundle", "verify", bundle_path),
        SSH + ["mkdir -p /home/nick/backups && ls -1 /home/nick/backups"],
        SSH + ["mkdir /home/nick/backups/deploy_20261110"],
        ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
         "rsc_main_bbbbbbbbbbbb.bundle", "option-server:" + remote_bundle],
        SSH + ["git -C /home/nick/option_trading bundle verify " + remote_bundle],
        SSH + ["git -C /home/nick/option_trading fetch --no-tags " + remote_bundle
               + " refs/remotes/origin/main:incoming-main"],
        SSH + ["git -C /home/nick/option_trading merge --ff-only incoming-main"],
        SSH + ["git -C /home/nick/option_trading rev-parse HEAD"],
        SSH + [SMOKE_COMMAND],
        _git("diff", "--shortstat", OLD_HEAD, TARGET),
    ]
    assert [call.arguments for call in runner.calls] == expected
    assert result.success is True
    assert result.old_head == OLD_HEAD
    assert result.new_head == TARGET
    assert result.files_changed_summary == "3 files changed, 10 insertions(+), 2 deletions(-)"
    assert caplog.records[-1].getMessage() == RESTART_REMINDER
    # No temporary tag was needed for the origin/main tip.
    assert not any(call.arguments[3:4] == ["tag"] for call in runner.calls)


def test_every_issued_command_passes_the_guard():
    runner = FakeRunner(origin_main=NEWER_ORIGIN_MAIN)
    _service(runner).stage(TARGET)
    for call in runner.calls:
        guard_command(call.arguments)


def test_literal_origin_main_is_accepted():
    runner = FakeRunner()
    result = _service(runner).stage("origin/main")
    assert result.success is True
    assert runner.calls[0].arguments == _git(
        "rev-parse", "--verify", "--quiet", "origin/main^{commit}"
    )


def test_older_commit_uses_a_temporary_tag_that_is_removed():
    runner = FakeRunner(origin_main=NEWER_ORIGIN_MAIN)
    result = _service(runner).stage(TARGET)

    assert result.success is True
    arguments = [call.arguments for call in runner.calls]
    tag_create = _git("tag", "deploy-staging-bbbbbbbbbbbb", TARGET)
    tag_delete = _git("tag", "-d", "deploy-staging-bbbbbbbbbbbb")
    assert tag_create in arguments and tag_delete in arguments
    assert arguments.index(tag_create) < arguments.index(tag_delete)
    bundle_create = next(a for a in arguments if a[3:5] == ["bundle", "create"])
    assert bundle_create[6:] == ["refs/tags/deploy-staging-bbbbbbbbbbbb", "^" + OLD_HEAD]
    fetch = next(c for c in runner.remote_commands() if " fetch " in c)
    assert fetch.endswith(" refs/tags/deploy-staging-bbbbbbbbbbbb:incoming-main")


def test_temporary_tag_is_removed_when_a_later_step_fails():
    class FailingCopyRunner(FakeRunner):
        def run(self, arguments, timeout_seconds, working_directory=None):
            if arguments[0] == "scp":
                self.calls.append(RecordedCall(list(arguments), timeout_seconds, working_directory))
                return CommandResult(1, "", "connection lost")
            return super().run(arguments, timeout_seconds, working_directory)

    runner = FailingCopyRunner(origin_main=NEWER_ORIGIN_MAIN)
    with pytest.raises(StagingError, match="connection lost"):
        _service(runner).stage(TARGET)
    assert runner.calls[-1].arguments == _git("tag", "-d", "deploy-staging-bbbbbbbbbbbb")
    assert not any("merge --ff-only" in c for c in runner.remote_commands())


def test_bundle_stays_inside_a_temporary_directory_that_is_removed():
    runner = FakeRunner()
    _service(runner).stage(TARGET)

    bundle_path = Path(_bundle_path(runner))
    scp_call = next(call for call in runner.calls if call.arguments[0] == "scp")
    assert bundle_path.parent == scp_call.working_directory
    assert bundle_path.parent.name.startswith("deploy_staging_")
    assert bundle_path.parent.parent == Path(tempfile.gettempdir())
    assert not bundle_path.parent.exists()


def test_existing_backup_folder_of_the_day_gets_a_suffix():
    runner = FakeRunner(backups_listing="deploy_20261110\ndeploy_20261110_2\n")
    _service(runner).stage(TARGET)
    assert "mkdir /home/nick/backups/deploy_20261110_3" in runner.remote_commands()


def test_already_staged_stops_after_the_read_only_checks():
    runner = FakeRunner(remote_head=TARGET)
    result = _service(runner).stage(TARGET)

    assert result.success is True
    assert result.message.startswith("Already staged")
    assert runner.remote_commands() == [
        "git -C /home/nick/option_trading rev-parse HEAD",
        "git -C /home/nick/option_trading symbolic-ref --short HEAD",
        "git -C /home/nick/option_trading status --porcelain",
    ]
    assert "scp" not in runner.programs()


def test_dirty_vps_tree_is_refused():
    runner = FakeRunner(remote_status=" M coding/service/foo.py\n")
    with pytest.raises(StagingError, match="not clean"):
        _service(runner).stage(TARGET)
    assert len(runner.remote_commands()) == 3
    assert "scp" not in runner.programs()


def test_other_branch_checked_out_on_the_vps_is_refused():
    runner = FakeRunner(remote_branch="incoming-main")
    with pytest.raises(StagingError, match="checked out"):
        _service(runner).stage(TARGET)
    assert "scp" not in runner.programs()


def test_target_not_merged_into_origin_main_is_refused():
    runner = FakeRunner(origin_main=OLD_HEAD)  # TARGET is newer than origin/main
    with pytest.raises(StagingError, match="not part of origin/main"):
        _service(runner).stage(TARGET)
    assert runner.programs() == ["git", "git", "git"]


def test_vps_head_not_an_ancestor_of_target_is_refused():
    runner = FakeRunner(ancestors={(TARGET, NEWER_ORIGIN_MAIN)})
    with pytest.raises(StagingError, match="not an ancestor"):
        _service(runner).stage(TARGET)
    assert "scp" not in runner.programs()
    assert not any("bundle" in a for call in runner.calls for a in call.arguments)


def test_vps_head_unknown_locally_is_refused():
    runner = FakeRunner(remote_head="d" * 40)
    with pytest.raises(StagingError, match="does not exist in the local repository"):
        _service(runner).stage(TARGET)
    assert "scp" not in runner.programs()


@pytest.mark.parametrize(
    "bad_commit",
    ["", "abc12", "BBBBBBB", "bbbbbbb; sudo reboot", "main", "b" * 41, "origin/main^", "-bbbbbbb"],
)
def test_invalid_commit_is_refused_before_any_command(bad_commit):
    runner = FakeRunner()
    with pytest.raises(StagingError, match="Invalid commit"):
        _service(runner).stage(bad_commit)
    assert runner.calls == []


def test_import_smoke_failure_returns_failure_with_rollback_hint(caplog):
    runner = FakeRunner(smoke_stdout="", smoke_return_code=1)
    with caplog.at_level(logging.INFO):
        result = _service(runner).stage(TARGET)

    assert result.success is False
    rollback = f"git -C /home/nick/option_trading reset --hard {OLD_HEAD}"
    assert rollback in result.message
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and rollback in errors[0].getMessage()
    # The rollback is only a hint: the smoke test was the last remote command.
    assert runner.remote_commands()[-1] == SMOKE_COMMAND
    for call in runner.calls:
        guard_command(call.arguments)


def test_import_smoke_without_marker_is_a_failure():
    runner = FakeRunner(smoke_stdout="Traceback ...\n", smoke_return_code=0)
    assert _service(runner).stage(TARGET).success is False


def test_dry_run_issues_no_ssh_or_scp(caplog):
    runner = FakeRunner()
    with caplog.at_level(logging.INFO):
        result = _service(runner).stage(TARGET, dry_run=True)

    assert result.success is True and result.dry_run is True
    assert runner.programs() == ["git", "git", "git"]
    assert all(
        call.arguments[3] in ("rev-parse", "merge-base") for call in runner.calls
    )
    planned = [r.getMessage() for r in caplog.records if "WOULD RUN" in r.getMessage()]
    assert any(message.startswith("  WOULD RUN: scp ") for message in planned)
    assert any("merge --ff-only incoming-main" in message for message in planned)


def test_dry_run_of_an_older_commit_plans_but_does_not_create_the_tag(caplog):
    runner = FakeRunner(origin_main=NEWER_ORIGIN_MAIN)
    with caplog.at_level(logging.INFO):
        _service(runner).stage(TARGET, dry_run=True)
    assert runner.programs() == ["git", "git", "git"]
    planned = [r.getMessage() for r in caplog.records if "WOULD RUN" in r.getMessage()]
    assert any(m.endswith(f"tag deploy-staging-bbbbbbbbbbbb {TARGET}") for m in planned)
    assert any(m.endswith("tag -d deploy-staging-bbbbbbbbbbbb") for m in planned)


def test_dry_run_still_refuses_unmerged_commits():
    runner = FakeRunner(origin_main=OLD_HEAD)
    with pytest.raises(StagingError, match="not part of origin/main"):
        _service(runner).stage(TARGET, dry_run=True)


@pytest.mark.parametrize("token", FORBIDDEN_REMOTE_TOKENS)
def test_guard_rejects_each_forbidden_token(token):
    with pytest.raises(ForbiddenCommandError):
        guard_command(SSH + [f"cd /home/nick/option_trading && {token} x"])


@pytest.mark.parametrize(
    "doctored",
    [
        SSH + ["sudo systemctl restart option-trading"],
        SSH + ["git -C /home/nick/option_trading reset --hard " + OLD_HEAD],
        SSH + ["cat /home/nick/option_trading/.env"],
        SSH + ["psql -c 'drop table x'"],
        ["bash", "-c", "echo hi"],
        [],
    ],
)
def test_guard_rejects_doctored_commands(doctored):
    with pytest.raises(ForbiddenCommandError):
        guard_command(doctored)


def test_a_doctored_template_is_rejected_before_it_runs(monkeypatch):
    from coding.service.deployment import vps_code_staging_service as module

    doctored_templates: Dict[str, str] = dict(module.REMOTE_COMMAND_TEMPLATES)
    doctored_templates["read_status"] = "git -C {repository} clean -fdx"
    monkeypatch.setattr(module, "REMOTE_COMMAND_TEMPLATES", doctored_templates)
    runner = FakeRunner()
    with pytest.raises(ForbiddenCommandError):
        _service(runner).stage(TARGET)
    assert not any("clean" in c for c in runner.remote_commands())


def test_venv_path_is_not_mistaken_for_dot_env():
    guard_command(SSH + [SMOKE_COMMAND])


class TestCommandLine:
    def test_commit_is_required(self):
        with pytest.raises(SystemExit) as raised:
            deploy_to_vps.parse_arguments([])
        assert raised.value.code == 2

    def test_arguments_are_parsed(self):
        parsed = deploy_to_vps.parse_arguments(["--commit", "origin/main", "--dry-run"])
        assert parsed.commit == "origin/main" and parsed.dry_run is True
        assert deploy_to_vps.parse_arguments(["--commit", TARGET]).dry_run is False

    def test_invalid_commit_exits_non_zero_without_running_anything(self, monkeypatch):
        monkeypatch.setattr(deploy_to_vps, "init_logging", lambda **_: None)

        def no_subprocess(*_arguments, **_keywords):
            raise AssertionError("no command may run for an invalid commit")

        monkeypatch.setattr(
            "coding.service.deployment.vps_code_staging_service.subprocess.run", no_subprocess
        )
        assert deploy_to_vps.main(["--commit", "not-a-hash"]) == 1
