import pytest

from zeffy import ZeffyClient, ZeffyError, extract_cursor, extract_items, has_more


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if not self._responses:
            raise AssertionError("unexpected extra request")
        return self._responses.pop(0)


def client_with(responses, **kw):
    return ZeffyClient(api_key="k", session=FakeSession(responses), **kw)


class TestEnvelopeParsing:
    @pytest.mark.parametrize("key", ["data", "items", "results", "campaigns", "records"])
    def test_accepts_known_envelope_keys(self, key):
        assert extract_items({key: [{"id": 1}]}) == [{"id": 1}]

    def test_accepts_bare_list(self):
        assert extract_items([{"id": 1}]) == [{"id": 1}]

    def test_unknown_shape_yields_nothing(self):
        assert extract_items({"unexpected": [{"id": 1}]}) == []

    @pytest.mark.parametrize("key", ["next_cursor", "nextCursor"])
    def test_cursor_keys(self, key):
        assert extract_cursor({key: "abc"}) == "abc"

    def test_has_more_explicit_false_wins_over_full_page(self):
        assert has_more({"has_more": False}, [{}] * 100, 100) is False

    def test_has_more_inferred_from_full_page(self):
        assert has_more({}, [{}] * 100, 100) is True
        assert has_more({}, [{}] * 3, 100) is False


class TestPagination:
    def test_follows_cursor_across_pages(self):
        session_responses = [
            FakeResponse(payload={"data": [{"id": "a"}], "has_more": True, "next_cursor": "c1"}),
            FakeResponse(payload={"data": [{"id": "b"}], "has_more": False}),
        ]
        client = client_with(session_responses)
        assert [r["id"] for r in client.iter_campaigns(page_size=1)] == ["a", "b"]
        assert client._session.calls[1]["params"]["starting_after"] == "c1"

    def test_stops_when_cursor_repeats(self):
        looping = [
            FakeResponse(payload={"data": [{"id": "a"}], "has_more": True, "next_cursor": "same"}),
            FakeResponse(payload={"data": [{"id": "b"}], "has_more": True, "next_cursor": "same"}),
        ]
        client = client_with(looping)
        # Must terminate rather than spin forever on a stuck cursor.
        assert [r["id"] for r in client.iter_campaigns(page_size=1)] == ["a", "b"]

    def test_falls_back_to_last_id_when_cursor_missing(self):
        responses = [
            FakeResponse(payload={"data": [{"id": "a"}], "has_more": True}),
            FakeResponse(payload={"data": [{"id": "b"}], "has_more": False}),
        ]
        client = client_with(responses)
        list(client.iter_campaigns(page_size=1))
        assert client._session.calls[1]["params"]["starting_after"] == "a"

    def test_respects_max_pages(self):
        endless = [
            FakeResponse(payload={"data": [{"id": str(i)}], "has_more": True,
                                  "next_cursor": f"c{i}"})
            for i in range(10)
        ]
        client = client_with(endless)
        assert len(list(client.iter_campaigns(page_size=1, max_pages=3))) == 3

    def test_caps_page_size_at_100(self):
        client = client_with([FakeResponse(payload={"data": [], "has_more": False})])
        list(client.iter_campaigns(page_size=500))
        assert client._session.calls[0]["params"]["limit"] == 100


class TestErrorHandling:
    def test_auth_failure_does_not_leak_body(self):
        # Zeffy may quote the rejected key back in the error body; the raised
        # exception must not carry it into logs.
        leaked = "deadbeef-0000-0000-0000-000000000000"
        client = client_with([FakeResponse(status_code=401, text=f"key {leaked} invalid")])
        with pytest.raises(ZeffyError) as excinfo:
            client.get("campaigns")
        assert leaked not in str(excinfo.value)
        assert "revoked" in str(excinfo.value)

    def test_retries_on_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("zeffy.time.sleep", lambda _: None)
        client = client_with([
            FakeResponse(status_code=429, headers={"Retry-After": "1"}),
            FakeResponse(payload={"data": [{"id": "a"}], "has_more": False}),
        ])
        assert extract_items(client.get("campaigns")) == [{"id": "a"}]

    def test_gives_up_after_max_retries(self, monkeypatch):
        monkeypatch.setattr("zeffy.time.sleep", lambda _: None)
        client = client_with([FakeResponse(status_code=503) for _ in range(5)],
                             max_retries=5)
        with pytest.raises(ZeffyError, match="after 5 attempts"):
            client.get("campaigns")

    def test_non_retryable_status_raises_immediately(self):
        client = client_with([FakeResponse(status_code=404, text="nope")])
        with pytest.raises(ZeffyError, match="404"):
            client.get("campaigns")

    def test_requires_api_key(self):
        with pytest.raises(ValueError):
            ZeffyClient(api_key="")

    def test_sends_bearer_header(self):
        session = FakeSession([])
        ZeffyClient(api_key="secret", session=session)
        assert session.headers["Authorization"] == "Bearer secret"
