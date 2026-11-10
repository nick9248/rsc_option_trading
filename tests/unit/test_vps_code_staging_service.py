"""
Tests for VpsCodeStagingService: command order, refusals and the safety guard.

A fake runner records every command and answers like git/ssh/scp would. No real
git, ssh or scp process is started.
"""
import logging
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pytest

from coding.service.deployment.vps_code_staging_service import (
    CONFIGURATION_NOTE,
    FORBIDDEN_REMOTE_TOKENS,
    RESTART_REMINDER,
    CommandResult,
    ForbiddenCommandError,
    StagingError,
    SubprocessCommandRunner,
    VpsCodeStagingService,
    VpsSettings,
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
    backups_missing: bool = False
    smoke_stdout: str = "imports OK\n"
    smoke_return_code: int = 0
    # Tags or branches whose names win over a hash prefix in git rev-parse.
    named_references: Dict[str, str] = field(default_factory=dict)
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
            if revision == "refs/remotes/origin/main":
                return CommandResult(0, self.origin_main + "\n", "")
            if revision in self.named_references:
                return CommandResult(0, self.named_references[revision] + "\n", "")
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
        if remote_command == "ls -1 /home/nick/backups":
            if self.backups_missing:
                return CommandResult(
                    2, "", "ls: cannot access '/home/nick/backups': No such file or directory\n"
                )
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
        SSH + ["ls -1 /home/nick/backups"],
        SSH + ["mkdir /home/nick/backups/deploy_20261110"],
        ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
         "rsc_main_bbbbbbbbbbbb.bundle", "option-server:" + remote_bundle],
        SSH + ["git -C /home/nick/option_trading bundle verify " + remote_bundle],
        SSH + ["git -C /home/nick/option_trading fetch --no-tags " + remote_bundle
               + " +refs/remotes/origin/main:incoming-main"],
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


def test_literal_origin_main_is_resolved_through_the_full_reference():
    # A local branch called "origin/main" pointing elsewhere must not shadow the remote one.
    runner = FakeRunner(named_references={"origin/main": OLD_HEAD})
    result = _service(runner).stage("origin/main")
    assert result.success is True
    assert result.new_head == TARGET
    assert runner.calls[0].arguments == _git(
        "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}"
    )
    assert not any("origin/main^{commit}" == a for call in runner.calls for a in call.arguments)


def test_older_commit_uses_a_temporary_tag_that_is_removed():
    runner = FakeRunner(origin_main=NEWER_ORIGIN_MAIN)
    result = _service(runner).stage(TARGET)

    assert result.success is True
    arguments = [call.arguments for call in runner.calls]
    tag_create = _git("tag", "deploy-staging-bbbbbbbbbbbb-20261110T183000", TARGET)
    tag_delete = _git("tag", "-d", "deploy-staging-bbbbbbbbbbbb-20261110T183000")
    assert tag_create in arguments and tag_delete in arguments
    assert arguments.index(tag_create) < arguments.index(tag_delete)
    bundle_create = next(a for a in arguments if a[3:5] == ["bundle", "create"])
    assert bundle_create[6:] == ["refs/tags/deploy-staging-bbbbbbbbbbbb-20261110T183000", "^" + OLD_HEAD]
    fetch = next(c for c in runner.remote_commands() if " fetch " in c)
    assert fetch.endswith(" +refs/tags/deploy-staging-bbbbbbbbbbbb-20261110T183000:incoming-main")


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
    assert runner.calls[-1].arguments == _git("tag", "-d", "deploy-staging-bbbbbbbbbbbb-20261110T183000")
    assert not any("merge --ff-only" in c for c in runner.remote_commands())


def test_failed_tag_creation_never_deletes_the_existing_tag():
    class ExistingTagRunner(FakeRunner):
        def _git(self, git_arguments):
            if git_arguments[0] == "tag":
                return CommandResult(128, "", "fatal: tag 'deploy-staging-bbbbbbbbbbbb-20261110T183000' already exists")
            return super()._git(git_arguments)

    runner = ExistingTagRunner(origin_main=NEWER_ORIGIN_MAIN)
    with pytest.raises(StagingError, match="already exists"):
        _service(runner).stage(TARGET)
    tag_calls = [call.arguments[3:] for call in runner.calls if call.arguments[3:4] == ["tag"]]
    assert tag_calls == [["tag", "deploy-staging-bbbbbbbbbbbb-20261110T183000", TARGET]]
    assert "scp" not in runner.programs()


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


def test_missing_backups_directory_is_a_clear_error_and_is_not_created():
    runner = FakeRunner(backups_missing=True)
    with pytest.raises(StagingError, match="backups directory missing on the VPS: create it by hand"):
        _service(runner).stage(TARGET)
    assert not any(c.startswith("mkdir") for c in runner.remote_commands())
    assert "scp" not in runner.programs()


def test_only_make_deploy_directory_writes_before_the_copy():
    runner = FakeRunner()
    _service(runner).stage(TARGET)
    remote = runner.remote_commands()
    writing = [c for c in remote if "mkdir" in c]
    assert writing == ["mkdir /home/nick/backups/deploy_20261110"]
    assert "-p" not in writing[0].split()


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


def test_diverged_vps_history_is_refused():
    # OLD_HEAD and TARGET: neither is an ancestor of the other.
    runner = FakeRunner(ancestors={(TARGET, NEWER_ORIGIN_MAIN)})
    with pytest.raises(StagingError, match="have diverged"):
        _service(runner).stage(TARGET)
    assert "scp" not in runner.programs()
    assert not any("bundle" in a for call in runner.calls for a in call.arguments)


def test_vps_ahead_of_the_target_is_success_without_staging():
    runner = FakeRunner(remote_head=NEWER_ORIGIN_MAIN, origin_main=NEWER_ORIGIN_MAIN)
    result = _service(runner).stage(TARGET)

    assert result.success is True
    assert result.message == (
        f"The VPS already contains {TARGET} (VPS HEAD {NEWER_ORIGIN_MAIN}). Nothing to stage."
    )
    assert result.new_head == NEWER_ORIGIN_MAIN
    assert runner.calls[-1].arguments == _git(
        "merge-base", "--is-ancestor", TARGET, NEWER_ORIGIN_MAIN
    )
    assert "scp" not in runner.programs()
    assert len(runner.remote_commands()) == 3


def test_vps_head_unknown_locally_is_refused():
    runner = FakeRunner(remote_head="d" * 40)
    with pytest.raises(StagingError, match="does not exist in the local repository"):
        _service(runner).stage(TARGET)
    assert "scp" not in runner.programs()


@pytest.mark.parametrize(
    "bad_commit",
    [
        "", "abc12", "BBBBBBB", "bbbbbbb; sudo reboot", "main", "b" * 41, "origin/main^",
        "-bbbbbbb", "bbbbbbb\n", TARGET + "\n",
    ],
)
def test_invalid_commit_is_refused_before_any_command(bad_commit):
    runner = FakeRunner()
    with pytest.raises(StagingError, match="Invalid commit"):
        _service(runner).stage(bad_commit)
    assert runner.calls == []


def test_a_tag_named_like_the_hash_is_refused():
    # A tag "bbbbbbb" points at OLD_HEAD; git would resolve the name, not the hash prefix.
    runner = FakeRunner(named_references={"bbbbbbb": OLD_HEAD})
    with pytest.raises(StagingError, match="does not start with it"):
        _service(runner).stage("bbbbbbb")
    assert runner.programs() == ["git"]


def test_full_hash_input_resolves_to_itself():
    runner = FakeRunner()
    assert _service(runner).stage(TARGET, dry_run=True).success is True


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


def test_ssh_exit_255_in_the_smoke_step_is_an_ssh_problem_not_an_import_failure():
    runner = FakeRunner(smoke_stdout="", smoke_return_code=255)
    result = _service(runner).stage(TARGET)

    assert result.success is False
    assert result.new_head == TARGET and result.old_head == OLD_HEAD
    assert result.message == (
        "Could not run the import smoke test (ssh problem: exit 255: boom). "
        f"The VPS code is now at {TARGET} (was {OLD_HEAD}); the service was not restarted. "
        "Run the check yourself or roll back on the VPS yourself: "
        f"git -C /home/nick/option_trading reset --hard {OLD_HEAD}"
    )
    assert "Import smoke test FAILED" not in result.message


def test_runner_error_in_the_smoke_step_does_not_escape():
    class TimeoutInSmokeRunner(FakeRunner):
        def run(self, arguments, timeout_seconds, working_directory=None):
            if arguments[-1] == SMOKE_COMMAND:
                raise StagingError("Command timed out after 120 s")
            return super().run(arguments, timeout_seconds, working_directory)

    result = _service(TimeoutInSmokeRunner()).stage(TARGET)
    assert result.success is False
    assert result.message.startswith(
        "Could not run the import smoke test (ssh problem: Command timed out after 120 s). "
        f"The VPS code is now at {TARGET} (was {OLD_HEAD})"
    )


def test_any_exception_after_the_fast_forward_does_not_escape():
    class BrokenAfterMergeRunner(FakeRunner):
        def run(self, arguments, timeout_seconds, working_directory=None):
            if self.remote_head == TARGET:  # the fast-forward has happened
                raise RuntimeError("connection reset")
            return super().run(arguments, timeout_seconds, working_directory)

    result = _service(BrokenAfterMergeRunner()).stage(TARGET)
    assert result.success is False
    assert "connection reset" in result.message
    assert f"reset --hard {OLD_HEAD}" in result.message


def test_genuine_import_failure_keeps_the_import_failure_message():
    runner = FakeRunner(smoke_stdout="", smoke_return_code=1)
    result = _service(runner).stage(TARGET)
    assert result.success is False
    assert result.message.startswith(f"Import smoke test FAILED after the fast-forward to {TARGET} (exit 1): boom.")


def test_import_smoke_without_marker_is_a_failure():
    runner = FakeRunner(smoke_stdout="Traceback ...\n", smoke_return_code=0)
    assert _service(runner).stage(TARGET).success is False


def test_dry_run_issues_no_ssh_or_scp(caplog):
    runner = FakeRunner()
    with caplog.at_level(logging.INFO):
        result = _service(runner).stage(TARGET, dry_run=True)

    assert result.success is True and result.dry_run is True
    assert result.new_head is None and result.old_head is None
    assert "No remote state was read" in result.message
    assert CONFIGURATION_NOTE in [r.getMessage() for r in caplog.records]
    assert "does not read, print or edit the VPS .env itself" in CONFIGURATION_NOTE
    assert "the real run may still refuse" in result.message
    assert runner.programs() == ["git", "git", "git"]
    assert all(
        call.arguments[3] in ("rev-parse", "merge-base") for call in runner.calls
    )
    planned = [r.getMessage() for r in caplog.records if "WOULD RUN" in r.getMessage()]
    assert any(message.startswith("  WOULD RUN: scp ") for message in planned)
    assert any("merge --ff-only incoming-main" in message for message in planned)
    assert any(message.endswith(" +refs/remotes/origin/main:incoming-main") for message in planned)


def test_dry_run_of_an_older_commit_plans_but_does_not_create_the_tag(caplog):
    runner = FakeRunner(origin_main=NEWER_ORIGIN_MAIN)
    with caplog.at_level(logging.INFO):
        _service(runner).stage(TARGET, dry_run=True)
    assert runner.programs() == ["git", "git", "git"]
    planned = [r.getMessage() for r in caplog.records if "WOULD RUN" in r.getMessage()]
    assert any(m.endswith(f"tag deploy-staging-bbbbbbbbbbbb-20261110T183000 {TARGET}") for m in planned)
    assert any(m.endswith("tag -d deploy-staging-bbbbbbbbbbbb-20261110T183000") for m in planned)


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


@pytest.mark.parametrize(
    "local_path",
    ["C:/work/clean_checkout", "C:/projects/.env_tools/repo", "D:/reset/repo"],
)
def test_local_paths_are_not_token_checked(local_path):
    guard_command(["git", "-C", local_path, "rev-parse", "--verify", "--quiet", "HEAD"])
    guard_command(["git", "-C", local_path, "bundle", "create", local_path + "/x.bundle",
                   "refs/remotes/origin/main", "^" + OLD_HEAD])
    guard_command(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                   local_path + "/x.bundle", "option-server:/home/nick/backups/x.bundle"])


@pytest.mark.parametrize("token", FORBIDDEN_REMOTE_TOKENS)
def test_scp_destination_with_a_forbidden_token_is_refused(token):
    with pytest.raises(ForbiddenCommandError):
        guard_command(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                       "x.bundle", f"option-server:/home/nick/{token}x"])


@pytest.mark.parametrize(
    "git_arguments",
    [
        ["reset", "--hard", OLD_HEAD],
        ["checkout", OLD_HEAD],
        ["clean", "-fdx"],
        ["push", "origin", "main"],
        ["tag", "v1.0", TARGET],
        ["tag", "-d", "v1.0"],
        ["tag", "deploy-staging-bbbbbbbbbbbb", TARGET],  # old form without the time
        ["tag", "-d", "deploy-staging-bbbbbbbbbbbb"],
        ["tag", "deploy-staging-bbbbbbbbbbbb-20261110T183000", "HEAD"],  # not a hash
        ["tag", "-f", "deploy-staging-bbbbbbbbbbbb-20261110T183000"],
    ],
)
def test_local_git_outside_the_allow_list_is_refused(git_arguments):
    with pytest.raises(ForbiddenCommandError):
        guard_command(["git", "-C", "C:/repo", *git_arguments])


def test_temporary_tag_name_is_unique_per_run():
    later = datetime(2026, 11, 10, 18, 30, 1, tzinfo=timezone.utc)
    names = []
    for clock_time in (FIXED_NOW, later):
        runner = FakeRunner(origin_main=NEWER_ORIGIN_MAIN)
        VpsCodeStagingService(
            runner=runner, local_repository=LOCAL_REPOSITORY, clock=lambda: clock_time
        ).stage(TARGET)
        names.append(next(c.arguments[4] for c in runner.calls if c.arguments[3:4] == ["tag"]))
    assert names == [
        "deploy-staging-bbbbbbbbbbbb-20261110T183000",
        "deploy-staging-bbbbbbbbbbbb-20261110T183001",
    ]


@pytest.mark.parametrize(
    "malformed",
    [
        ["ssh", "option-server", "git status"],  # options missing
        SSH[:-1] + ["-o", "ProxyCommand=evil", "option-server", "git status"],
        ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "a.bundle", "b.bundle"],
        ["git", "rev-parse", "HEAD"],  # no -C
    ],
)
def test_malformed_commands_are_refused(malformed):
    with pytest.raises(ForbiddenCommandError):
        guard_command(malformed)


def test_real_runner_replaces_undecodable_output():
    # A tiny local python process (not git, ssh or scp) writes invalid UTF-8.
    result = SubprocessCommandRunner().run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe ok')"],
        timeout_seconds=30,
    )
    assert result.return_code == 0
    assert result.stdout == "�� ok"


def test_settings_use_the_same_ssh_host_as_the_sync_script():
    # sync_from_vps.py has no remote repository path constant (it only inlines
    # /home/nick/option_trading/logs/... in one command), so only the host is compared.
    from scripts import sync_from_vps

    assert VpsSettings().host_alias == sync_from_vps.SSH_HOST


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
