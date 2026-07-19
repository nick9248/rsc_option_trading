from coding.core.health.models import CheckStatus
from coding.service.health.checkers.api_checker import ApiConnectivityCheck


class _FakeApiService:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_ticker(self, instrument_name):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_pass_when_ticker_has_index_price():
    check = ApiConnectivityCheck(api_service_factory=lambda: _FakeApiService({"index_price": 118000.0}))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.PASS
    assert "118" in results[0].message


def test_fail_when_response_missing_index_price():
    check = ApiConnectivityCheck(api_service_factory=lambda: _FakeApiService({}))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL


def test_fail_when_api_raises():
    check = ApiConnectivityCheck(api_service_factory=lambda: _FakeApiService(ConnectionError("down")))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL
    assert "down" in results[0].message
