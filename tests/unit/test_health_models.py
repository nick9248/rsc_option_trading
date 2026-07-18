from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus


def test_check_status_has_pass_warn_fail():
    assert CheckStatus.PASS.value == "pass"
    assert CheckStatus.WARN.value == "warn"
    assert CheckStatus.FAIL.value == "fail"


def test_check_environment_has_local_vps_both():
    assert CheckEnvironment.LOCAL.value == "local"
    assert CheckEnvironment.VPS.value == "vps"
    assert CheckEnvironment.BOTH.value == "both"


def test_check_result_defaults_to_empty_details():
    result = CheckResult(name="test", status=CheckStatus.PASS, message="ok")
    assert result.details == {}


def test_check_result_holds_details():
    result = CheckResult(name="test", status=CheckStatus.WARN, message="stale", details={"hours_ago": 5.0})
    assert result.details["hours_ago"] == 5.0
