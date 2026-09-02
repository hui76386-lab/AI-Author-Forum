from unittest.mock import MagicMock, patch

from ..management.commands.reader_capacity_probe import _request
from ..management.commands.reader_synthetic_check import _fetch


def _response(body=b"{}"):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.headers = {}
    response.read.return_value = body
    return response


@patch(
    "ai_author_forum.reader_interactions.management.commands."
    "reader_synthetic_check.urlopen"
)
def test_synthetic_supports_internal_origin_with_explicit_public_host(urlopen):
    urlopen.return_value = _response()

    _fetch(
        "http://nginx", "/reader-api/v1/session/", timeout=1, host_header="site.test"
    )

    request = urlopen.call_args.args[0]
    assert request.get_header("Host") == "site.test"


@patch(
    "ai_author_forum.reader_interactions.management.commands."
    "reader_capacity_probe.urlopen"
)
def test_capacity_probe_supports_internal_origin_with_explicit_public_host(urlopen):
    urlopen.return_value = _response()

    status, _duration = _request("http://nginx/", 1, "site.test")

    request = urlopen.call_args.args[0]
    assert status == 200
    assert request.get_header("Host") == "site.test"
