"""
Stage merged code on the VPS with a git bundle.

The VPS cannot fetch from GitHub, so code reaches it as an incremental git bundle:
build the bundle on this PC, copy it to the VPS with scp, fetch it into the branch
``incoming-main`` there and fast-forward ``main``. Afterwards an import smoke test
runs with the VPS virtual environment.

The service is deliberately narrow. Every remote command comes from the fixed
templates in ``REMOTE_COMMAND_TEMPLATES``; the only values filled in are the
validated commit hash, the generated bundle path and the UTC date. Every command
(local and remote) passes ``guard_command`` before it runs. The service never
restarts the collector, never touches ``.env``, never runs migrations and never
resets, cleans or checks out anything on the VPS. Restarting the service is left
to the owner.
"""

import logging
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

# Substrings that must never appear in any command the service issues. Checked
# case-insensitively, so "drop" and "DROP" are both refused.
FORBIDDEN_REMOTE_TOKENS = (
    "sudo",
    "systemctl",
    ".env",
    "psql",
    "pg_dump",
    "run_migration",
    "rm ",
    "reset",
    "clean",
    "checkout",
    "DROP",
    "TRUNCATE",
)

# Programs the service may start at all.
ALLOWED_PROGRAMS = ("git", "ssh", "scp")

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ORIGIN_MAIN = "origin/main"
ORIGIN_MAIN_REFERENCE = "refs/remotes/origin/main"
INCOMING_BRANCH = "incoming-main"
REMOTE_BRANCH = "main"
TEMPORARY_TAG_PREFIX = "deploy-staging-"
SHORT_COMMIT_LENGTH = 12

SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=15")

LOCAL_GIT_TIMEOUT_SECONDS = 60
BUNDLE_TIMEOUT_SECONDS = 300
REMOTE_TIMEOUT_SECONDS = 120
COPY_TIMEOUT_SECONDS = 600

IMPORT_SMOKE_CODE = (
    "import logging; logging.disable(logging.CRITICAL); "
    "from coding.service.data_collection.unified_scheduler import UnifiedScheduler; "
    'print("imports OK")'
)
IMPORT_SMOKE_MARKER = "imports OK"

RESTART_REMINDER = (
    "Code staged. The running service is NOT restarted: "
    "run sudo systemctl restart option-trading yourself."
)

# The only remote commands the service can send. Placeholders are filled with
# shlex-quoted values; nothing else from the caller reaches the remote shell.
REMOTE_COMMAND_TEMPLATES: Dict[str, str] = {
    "read_head": "git -C {repository} rev-parse HEAD",
    "read_branch": "git -C {repository} symbolic-ref --short HEAD",
    "read_status": "git -C {repository} status --porcelain",
    "list_backups": "mkdir -p {backups} && ls -1 {backups}",
    "make_deploy_directory": "mkdir {deploy_directory}",
    "verify_bundle": "git -C {repository} bundle verify {bundle}",
    "fetch_bundle": (
        "git -C {repository} fetch --no-tags {bundle} {bundle_reference}:" + INCOMING_BRANCH
    ),
    "fast_forward": "git -C {repository} merge --ff-only " + INCOMING_BRANCH,
    "import_smoke": "cd {repository} && {python} -c {smoke_code}",
}


@dataclass(frozen=True)
class VpsSettings:
    """Fixed facts about the VPS the code is staged on."""

    host_alias: str = "option-server"
    remote_repository_directory: str = "/home/nick/option_trading"
    remote_backups_directory: str = "/home/nick/backups"
    remote_python_path: str = "/home/nick/option_trading/.venv/bin/python"


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one external command."""

    return_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Runs one external command given as an argument list (never through a shell)."""

    def run(
        self,
        arguments: Sequence[str],
        timeout_seconds: float,
        working_directory: Optional[Path] = None,
    ) -> CommandResult:
        """Run the command and return its result."""
        ...


class StagingError(Exception):
    """Staging stopped: a precondition failed or a command did not succeed."""


class ForbiddenCommandError(StagingError):
    """A command contained a forbidden token or program and was not run."""


class SubprocessCommandRunner:
    """Real runner: subprocess with an argument list, no shell, with a timeout."""

    def run(
        self,
        arguments: Sequence[str],
        timeout_seconds: float,
        working_directory: Optional[Path] = None,
    ) -> CommandResult:
        """Run the command; a timeout or a missing program becomes a StagingError."""
        try:
            completed = subprocess.run(
                list(arguments),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=working_directory,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise StagingError(
                f"Command timed out after {timeout_seconds} s: {' '.join(arguments)}"
            ) from error
        except OSError as error:
            raise StagingError(f"Command could not start: {' '.join(arguments)}: {error}") from error
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class StagingResult:
    """What stage() did. success is False only when the import smoke test failed."""

    success: bool
    message: str
    old_head: Optional[str] = None
    new_head: Optional[str] = None
    files_changed_summary: Optional[str] = None
    dry_run: bool = False


def guard_command(arguments: Sequence[str]) -> None:
    """
    Raise ForbiddenCommandError unless the command is allowed to run.

    The program must be one of ALLOWED_PROGRAMS, and the whole command line
    (arguments joined by spaces) must not contain any FORBIDDEN_REMOTE_TOKENS.
    """
    if not arguments:
        raise ForbiddenCommandError("Empty command refused")
    if arguments[0] not in ALLOWED_PROGRAMS:
        raise ForbiddenCommandError(f"Program not allowed: {arguments[0]!r}")
    command_line = " ".join(arguments).lower() + " "
    for token in FORBIDDEN_REMOTE_TOKENS:
        if token.lower() in command_line:
            raise ForbiddenCommandError(
                f"Command refused, it contains the forbidden token {token!r}: {' '.join(arguments)}"
            )


def _default_local_repository() -> Path:
    """The repository this file belongs to (coding/service/deployment/ -> repo root)."""
    return Path(__file__).resolve().parents[3]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VpsCodeStagingService:
    """Stage a merged commit on the VPS as a fast-forward of its main branch."""

    def __init__(
        self,
        runner: CommandRunner,
        settings: VpsSettings = VpsSettings(),
        local_repository: Optional[Path] = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """
        Args:
            runner: Executes commands. Tests pass a fake that records them.
            settings: The VPS host alias and paths.
            local_repository: The local git repository that builds the bundle.
            clock: Returns the current UTC-aware time (names the backup folder).
        """
        self._runner = runner
        self._settings = settings
        self._local_repository = local_repository or _default_local_repository()
        self._clock = clock

    # ------------------------------------------------------------------ public

    def stage(self, target_commit: str, dry_run: bool = False) -> StagingResult:
        """
        Stage target_commit on the VPS.

        Args:
            target_commit: A hex commit hash (7 to 40 characters) or the literal
                "origin/main". It must be merged, that is an ancestor of (or equal to)
                the local origin/main.
            dry_run: Run only the read-only local git checks and log every other
                command instead of running it. No ssh or scp is started.

        Returns:
            StagingResult. success is False when the import smoke test failed
            after the fast-forward (the log then names the rollback command).

        Raises:
            StagingError: A precondition failed or a command did not succeed.
                Until the fast-forward succeeds the VPS code is unchanged (at most a
                backup folder and the incoming-main branch were written).
        """
        target, origin_main = self._resolve_target(target_commit)
        if dry_run:
            return self._log_dry_run_plan(target, target_is_origin_main=target == origin_main)

        old_head = self._check_remote_state(target)
        if old_head == target:
            message = f"Already staged: the VPS HEAD is {target}. Nothing to do."
            logger.info(message)
            return StagingResult(True, message, old_head=old_head, new_head=old_head)
        return self._stage_bundle(target, old_head, target_is_origin_main=target == origin_main)

    # ------------------------------------------------------- step (a): target

    def _resolve_target(self, target_commit: str) -> Tuple[str, str]:
        """
        Validate the argument, resolve it locally and require it to be merged.

        Returns (full target hash, full origin/main hash).
        """
        logger.info("Step 1: validate and resolve the target commit %r", target_commit)
        if target_commit != ORIGIN_MAIN and not COMMIT_PATTERN.match(target_commit):
            raise StagingError(
                f"Invalid commit {target_commit!r}: expected 7 to 40 lowercase hex "
                f"characters or {ORIGIN_MAIN!r}"
            )
        target = self._resolve_local_commit(target_commit)
        origin_main = self._resolve_local_commit(ORIGIN_MAIN_REFERENCE)
        if not self._is_local_ancestor(target, origin_main):
            raise StagingError(
                f"Refused: {target} is not part of origin/main ({origin_main}). Only merged "
                "code can be staged. If it was merged recently, run 'git fetch origin' first."
            )
        logger.info("Target %s is part of origin/main (%s)", target, origin_main)
        return target, origin_main

    def _resolve_local_commit(self, revision: str) -> str:
        result = self._run_local_git(
            ["rev-parse", "--verify", "--quiet", revision + "^{commit}"]
        )
        full_commit = result.stdout.strip()
        if result.return_code != 0 or not FULL_COMMIT_PATTERN.match(full_commit):
            raise StagingError(f"Commit {revision!r} does not exist in the local repository")
        return full_commit

    def _is_local_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._run_local_git(["merge-base", "--is-ancestor", ancestor, descendant])
        if result.return_code == 0:
            return True
        if result.return_code == 1:
            return False
        raise StagingError(
            f"git merge-base failed ({result.return_code}): {result.stderr.strip()}"
        )

    # ------------------------------------------------- step (b): remote checks

    def _check_remote_state(self, target: str) -> str:
        """Read-only checks on the VPS. Returns the VPS HEAD."""
        logger.info("Step 2: read-only checks on the VPS")
        remote_head = self._run_remote_or_fail("read_head").stdout.strip()
        if not FULL_COMMIT_PATTERN.match(remote_head):
            raise StagingError(f"The VPS HEAD is not a commit hash: {remote_head!r}")
        logger.info("VPS HEAD: %s", remote_head)

        branch = self._run_remote_or_fail("read_branch").stdout.strip()
        if branch != REMOTE_BRANCH:
            raise StagingError(
                f"Refused: the VPS has {branch!r} checked out, expected {REMOTE_BRANCH!r}"
            )

        status = self._run_remote_or_fail("read_status").stdout
        if status.strip():
            raise StagingError(
                "Refused: the VPS working tree is not clean (git status --porcelain):\n"
                + status.rstrip()
            )
        logger.info("VPS working tree is clean, branch %s", branch)

        if remote_head == target:
            return remote_head

        known = self._run_local_git(
            ["rev-parse", "--verify", "--quiet", remote_head + "^{commit}"]
        )
        if known.return_code != 0:
            raise StagingError(
                f"Refused: the VPS HEAD {remote_head} does not exist in the local repository"
            )
        if not self._is_local_ancestor(remote_head, target):
            raise StagingError(
                f"Refused: the VPS HEAD {remote_head} is not an ancestor of {target}, "
                "so a fast-forward is impossible"
            )
        logger.info("Fast-forward possible: %s -> %s", remote_head, target)
        return remote_head

    # ---------------------------------------------- steps (c) to (f): staging

    def _stage_bundle(
        self, target: str, old_head: str, target_is_origin_main: bool
    ) -> StagingResult:
        short = target[:SHORT_COMMIT_LENGTH]
        temporary_tag: Optional[str] = None
        try:
            with tempfile.TemporaryDirectory(prefix="deploy_staging_") as temporary_directory:
                logger.info("Step 3: build the bundle locally")
                if target_is_origin_main:
                    bundle_reference = ORIGIN_MAIN_REFERENCE
                else:
                    temporary_tag = TEMPORARY_TAG_PREFIX + short
                    self._run_local_git_or_fail(["tag", temporary_tag, target])
                    bundle_reference = "refs/tags/" + temporary_tag
                bundle_name = f"rsc_main_{short}.bundle"
                bundle_path = Path(temporary_directory) / bundle_name
                self._run_local_git_or_fail(
                    ["bundle", "create", str(bundle_path), bundle_reference, "^" + old_head],
                    timeout_seconds=BUNDLE_TIMEOUT_SECONDS,
                )
                self._run_local_git_or_fail(
                    ["bundle", "verify", str(bundle_path)],
                    timeout_seconds=BUNDLE_TIMEOUT_SECONDS,
                )

                logger.info("Step 4: copy the bundle to the VPS")
                deploy_directory = self._make_remote_deploy_directory()
                remote_bundle = f"{deploy_directory}/{bundle_name}"
                self._run_or_fail(
                    ["scp", *SSH_OPTIONS, bundle_name,
                     f"{self._settings.host_alias}:{remote_bundle}"],
                    timeout_seconds=COPY_TIMEOUT_SECONDS,
                    working_directory=Path(temporary_directory),
                )

                logger.info("Step 5: fetch and fast-forward on the VPS")
                self._run_remote_or_fail("verify_bundle", bundle=remote_bundle)
                self._run_remote_or_fail(
                    "fetch_bundle", bundle=remote_bundle, bundle_reference=bundle_reference
                )
                self._run_remote_or_fail("fast_forward")
        finally:
            if temporary_tag is not None:
                self._remove_temporary_tag(temporary_tag)

        new_head = self._run_remote_or_fail("read_head").stdout.strip()
        if new_head != target:
            raise StagingError(
                f"After the fast-forward the VPS HEAD is {new_head!r}, expected {target}"
            )

        logger.info("Step 6: import smoke test with the VPS virtual environment")
        smoke = self._run_remote("import_smoke")
        if smoke.return_code != 0 or IMPORT_SMOKE_MARKER not in smoke.stdout:
            rollback = (
                f"git -C {self._settings.remote_repository_directory} reset --hard {old_head}"
            )
            message = (
                f"Import smoke test FAILED after the fast-forward to {target} "
                f"(exit {smoke.return_code}): {smoke.stderr.strip() or smoke.stdout.strip()}. "
                f"The VPS code is now at {target} but the service was not restarted. "
                f"To roll back, run on the VPS yourself: {rollback}"
            )
            logger.error(message)
            return StagingResult(False, message, old_head=old_head, new_head=new_head)

        summary = self._run_local_git_or_fail(
            ["diff", "--shortstat", old_head, new_head]
        ).stdout.strip()
        logger.info("Old HEAD: %s", old_head)
        logger.info("New HEAD: %s", new_head)
        logger.info("Changes: %s", summary or "no file changes")
        logger.info(RESTART_REMINDER)
        return StagingResult(
            True, RESTART_REMINDER, old_head=old_head, new_head=new_head,
            files_changed_summary=summary,
        )

    def _make_remote_deploy_directory(self) -> str:
        """Create <backups>/deploy_<UTC date>[_n] on the VPS and return its path."""
        listing = self._run_remote_or_fail("list_backups").stdout.split()
        base_name = "deploy_" + self._clock().astimezone(timezone.utc).strftime("%Y%m%d")
        name = base_name
        counter = 2
        while name in listing:
            name = f"{base_name}_{counter}"
            counter += 1
        deploy_directory = f"{self._settings.remote_backups_directory}/{name}"
        self._run_remote_or_fail("make_deploy_directory", deploy_directory=deploy_directory)
        return deploy_directory

    def _remove_temporary_tag(self, tag: str) -> None:
        try:
            result = self._run_local_git(["tag", "-d", tag])
        except StagingError as error:
            logger.error("Could not remove the temporary tag %s: %s", tag, error)
            return
        if result.return_code != 0:
            logger.error(
                "Could not remove the temporary tag %s: %s", tag, result.stderr.strip()
            )

    # ---------------------------------------------------------------- dry run

    def _log_dry_run_plan(self, target: str, target_is_origin_main: bool) -> StagingResult:
        """Log the commands a real run would issue; run none of them."""
        settings = self._settings
        short = target[:SHORT_COMMIT_LENGTH]
        bundle_name = f"rsc_main_{short}.bundle"
        deploy_directory = (
            f"{settings.remote_backups_directory}/deploy_"
            + self._clock().astimezone(timezone.utc).strftime("%Y%m%d")
        )
        remote_bundle = f"{deploy_directory}/{bundle_name}"
        temporary_tag = TEMPORARY_TAG_PREFIX + short
        bundle_reference = (
            ORIGIN_MAIN_REFERENCE if target_is_origin_main else "refs/tags/" + temporary_tag
        )
        planned: List[List[str]] = [
            self._remote_arguments("read_head"),
            self._remote_arguments("read_branch"),
            self._remote_arguments("read_status"),
            self._local_git_arguments(["merge-base", "--is-ancestor", "<VPS_HEAD>", target]),
        ]
        if not target_is_origin_main:
            planned.append(self._local_git_arguments(["tag", temporary_tag, target]))
        planned += [
            self._local_git_arguments(
                ["bundle", "create", f"<temporary directory>/{bundle_name}",
                 bundle_reference, "^<VPS_HEAD>"]
            ),
            self._local_git_arguments(["bundle", "verify", f"<temporary directory>/{bundle_name}"]),
            self._remote_arguments("list_backups"),
            self._remote_arguments("make_deploy_directory", deploy_directory=deploy_directory),
            ["scp", *SSH_OPTIONS, bundle_name, f"{settings.host_alias}:{remote_bundle}"],
            self._remote_arguments("verify_bundle", bundle=remote_bundle),
            self._remote_arguments(
                "fetch_bundle", bundle=remote_bundle, bundle_reference=bundle_reference
            ),
            self._remote_arguments("fast_forward"),
        ]
        if not target_is_origin_main:
            planned.append(self._local_git_arguments(["tag", "-d", temporary_tag]))
        planned += [
            self._remote_arguments("read_head"),
            self._remote_arguments("import_smoke"),
            self._local_git_arguments(["diff", "--shortstat", "<VPS_HEAD>", target]),
        ]
        logger.info("DRY RUN: target %s; these commands WOULD run (none were started):", target)
        for arguments in planned:
            guard_command(arguments)
            logger.info("  WOULD RUN: %s", " ".join(arguments))
        message = f"Dry run finished for {target}. Nothing was copied or changed."
        logger.info(message)
        return StagingResult(True, message, new_head=target, dry_run=True)

    # ------------------------------------------------------- command building

    def _local_git_arguments(self, git_arguments: Sequence[str]) -> List[str]:
        return ["git", "-C", str(self._local_repository), *git_arguments]

    def _remote_arguments(self, template_name: str, **values: str) -> List[str]:
        """Build the ssh command for one fixed template; all values are shell-quoted."""
        settings = self._settings
        fields = {
            "repository": settings.remote_repository_directory,
            "backups": settings.remote_backups_directory,
            "python": settings.remote_python_path,
            "smoke_code": IMPORT_SMOKE_CODE,
            **values,
        }
        quoted = {name: shlex.quote(value) for name, value in fields.items()}
        remote_command = REMOTE_COMMAND_TEMPLATES[template_name].format(**quoted)
        return ["ssh", *SSH_OPTIONS, settings.host_alias, remote_command]

    # -------------------------------------------------------- command running

    def _run(
        self,
        arguments: Sequence[str],
        timeout_seconds: float,
        working_directory: Optional[Path] = None,
    ) -> CommandResult:
        guard_command(arguments)
        logger.info("RUN: %s", " ".join(arguments))
        return self._runner.run(arguments, timeout_seconds, working_directory)

    def _run_or_fail(
        self,
        arguments: Sequence[str],
        timeout_seconds: float,
        working_directory: Optional[Path] = None,
    ) -> CommandResult:
        result = self._run(arguments, timeout_seconds, working_directory)
        if result.return_code != 0:
            raise StagingError(
                f"Command failed ({result.return_code}): {' '.join(arguments)}\n"
                f"{result.stderr.strip()}"
            )
        return result

    def _run_local_git(
        self, git_arguments: Sequence[str], timeout_seconds: float = LOCAL_GIT_TIMEOUT_SECONDS
    ) -> CommandResult:
        return self._run(self._local_git_arguments(git_arguments), timeout_seconds)

    def _run_local_git_or_fail(
        self, git_arguments: Sequence[str], timeout_seconds: float = LOCAL_GIT_TIMEOUT_SECONDS
    ) -> CommandResult:
        return self._run_or_fail(self._local_git_arguments(git_arguments), timeout_seconds)

    def _run_remote(self, template_name: str, **values: str) -> CommandResult:
        return self._run(self._remote_arguments(template_name, **values), REMOTE_TIMEOUT_SECONDS)

    def _run_remote_or_fail(self, template_name: str, **values: str) -> CommandResult:
        return self._run_or_fail(
            self._remote_arguments(template_name, **values), REMOTE_TIMEOUT_SECONDS
        )
