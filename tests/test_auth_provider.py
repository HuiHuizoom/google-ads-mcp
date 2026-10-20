import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2 import MismatchingStateError

from team_ads_mcp import auth
from team_ads_mcp.config import ADS_SCOPE
from team_ads_mcp.errors import AdsError, safe_error
from team_ads_mcp.provider import GoogleProvider

from .fakes import ACCOUNT, FakeProvider, settings

CLIENT = {
    "installed": {
        "client_id": "offline-example.apps.googleusercontent.com",
        "client_secret": "not-real",
        "project_id": "test-project",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


def test_oauth_pkce_and_callback_state_rejection():
    flow = InstalledAppFlow.from_client_config(CLIENT, [ADS_SCOPE], autogenerate_code_verifier=True)
    flow.redirect_uri = "http://127.0.0.1:54321/"
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    params = parse_qs(urlparse(url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == [state]
    assert params["scope"] == [ADS_SCOPE]
    assert flow.code_verifier and len(flow.code_verifier) >= 43
    with pytest.raises(MismatchingStateError):
        flow.fetch_token(authorization_response="https://127.0.0.1:54321/?state=wrong&code=dummy")


def test_client_ownership_and_endpoints(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(CLIENT))
    assert auth.load_client_config(path, settings()) == CLIENT
    data = json.loads(json.dumps(CLIENT))
    data["installed"]["token_uri"] = "https://attacker.example/token"
    path.write_text(json.dumps(data))
    with pytest.raises(AdsError) as exc:
        auth.load_client_config(path, settings())
    assert exc.value.code == "OAUTH_ENDPOINT_REJECTED"
    data["installed"]["project_id"] = "other-project"
    path.write_text(json.dumps(data))
    with pytest.raises(AdsError) as exc:
        auth.load_client_config(path, settings())
    assert exc.value.code == "OAUTH_PROJECT_MISMATCH"


def test_refresh_failure_is_redacted_and_no_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auth.CredentialStore, "read", lambda _: {"cloud_project": "test-project", "credentials": {}}
    )
    creds = Mock(valid=False)
    creds.refresh.side_effect = RefreshError("invalid_grant refresh_token=SECRET")
    monkeypatch.setattr(Credentials, "from_authorized_user_info", Mock(return_value=creds))
    with pytest.raises(AdsError) as exc:
        auth.credentials(tmp_path, settings())
    assert exc.value.code == "AUTH_RECONNECT_REQUIRED"
    assert "SECRET" not in json.dumps(exc.value.public())


def test_refresh_persists_to_os_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auth.CredentialStore, "read", lambda _: {"cloud_project": "test-project", "credentials": {}}
    )
    creds = Mock(valid=False)
    creds.to_json.return_value = '{"refresh_token":"fake-for-test"}'
    monkeypatch.setattr(Credentials, "from_authorized_user_info", Mock(return_value=creds))
    saved = []
    monkeypatch.setattr(auth.CredentialStore, "write", lambda _, x: saved.append(x))
    assert auth.credentials(tmp_path, settings()) is creds
    creds.refresh.assert_called_once()
    assert saved[0]["cloud_project"] == "test-project"


def test_plaintext_keyring_is_rejected(monkeypatch):
    monkeypatch.setattr(auth.keyring, "get_keyring", lambda: object())
    with pytest.raises(AdsError) as exc:
        auth.CredentialStore.__new__(auth.CredentialStore)._backend()
    assert exc.value.code == "SECURE_STORE_UNAVAILABLE"


def test_mutate_adapter_disables_retries_and_partial_failure(tmp_path, monkeypatch):
    provider = GoogleProvider(tmp_path, settings())
    sdk = FakeProvider().sdk
    response = sdk.get_type("MutateGoogleAdsResponse")
    service = Mock()
    service.mutate.return_value = response
    client = Mock()
    client.get_type.side_effect = sdk.get_type
    client.get_service.return_value = service
    monkeypatch.setattr(provider, "client", lambda _: client)
    provider.mutate(ACCOUNT, [], validate_only=True)
    kwargs = service.mutate.call_args.kwargs
    assert kwargs["retry"] is None
    assert not kwargs["request"].partial_failure
    assert kwargs["request"].validate_only
    assert kwargs["request"].customer_id == ACCOUNT.customer_id
    response.partial_failure_error.code = 3
    with pytest.raises(AdsError) as exc:
        provider.mutate(ACCOUNT, [], validate_only=False)
    assert exc.value.code == "PARTIAL_FAILURE"


def test_arbitrary_error_no_secret_leak():
    result = safe_error(ValueError("client_secret=SECRET")).public()
    assert "SECRET" not in json.dumps(result)


def test_field_metadata_uses_valid_separate_queries_and_literal_underscores(tmp_path, monkeypatch):
    provider = GoogleProvider(tmp_path, settings())
    sdk = FakeProvider().sdk
    service = Mock()
    service.search_google_ads_fields.side_effect = [
        [type(sdk.get_type("GoogleAdsField"))(name="ad_group", selectable=True)],
        [type(sdk.get_type("GoogleAdsField"))(name="ad_group.name", selectable=True)],
    ]
    client = Mock()
    client.get_service.return_value = service
    monkeypatch.setattr(provider, "client", lambda account=None: client)
    assert [r["name"] for r in provider.fields("ad_group")] == ["ad_group", "ad_group.name"]
    queries = [c.kwargs["query"] for c in service.search_google_ads_fields.call_args_list]
    assert queries[0].endswith("WHERE name = 'ad_group'")
    assert queries[1].endswith("WHERE name LIKE 'ad[_]group.%'")
    assert all(" OR " not in query and " FROM " not in query for query in queries)


def test_request_scope_reuses_warmed_service_and_reloads_credentials(tmp_path, monkeypatch):
    from team_ads_mcp import provider as module

    sdk = FakeProvider().sdk
    clients = [Mock(), Mock()]
    for client in clients:
        client.get_type.side_effect = sdk.get_type
        client.get_service.return_value.search.return_value = []
        client.get_service.return_value.mutate.return_value = sdk.get_type(
            "MutateGoogleAdsResponse"
        )
    factory = Mock(side_effect=clients)
    credentials = Mock(side_effect=[Mock(), Mock()])
    monkeypatch.setattr(module, "GoogleAdsClient", factory)
    monkeypatch.setattr(module, "credentials", credentials)
    provider = GoogleProvider(tmp_path, settings())
    with provider.request_scope():
        provider.search(ACCOUNT, "SELECT customer.id FROM customer")
        with provider.request_scope():
            provider.mutate(ACCOUNT, [], validate_only=True)
        provider.mutate(ACCOUNT, [], validate_only=False)
        clients[0].get_service.return_value.transport.close.assert_not_called()
    assert factory.call_count == credentials.call_count == 1
    clients[0].get_service.assert_called_once_with("GoogleAdsService")
    assert clients[0].get_service.return_value.mutate.call_count == 2
    assert all(
        c.kwargs["retry"] is None for c in clients[0].get_service.return_value.mutate.call_args_list
    )
    clients[0].get_service.return_value.transport.close.assert_called_once()
    with provider.request_scope():
        provider.search(ACCOUNT, "SELECT customer.id FROM customer")
    assert factory.call_count == credentials.call_count == 2
    clients[1].get_service.return_value.transport.close.assert_called_once()


def test_request_scope_isolates_login_routes_and_closes_on_failure(tmp_path, monkeypatch):
    from team_ads_mcp import provider as module
    from team_ads_mcp.models import Account

    factory = Mock(side_effect=lambda **kwargs: Mock())
    monkeypatch.setattr(module, "GoogleAdsClient", factory)
    monkeypatch.setattr(module, "credentials", Mock())
    provider = GoogleProvider(tmp_path, settings())
    with pytest.raises(ValueError, match="original"), provider.request_scope():
        manager = provider.service("GoogleAdsService", ACCOUNT)
        direct = provider.service("GoogleAdsService", Account(customer_id=ACCOUNT.customer_id))
        assert manager is not direct
        assert provider.service("GoogleAdsService", ACCOUNT) is manager
        direct.transport.close.side_effect = RuntimeError("close failed")
        raise ValueError("original")
    assert [c.kwargs["login_customer_id"] for c in factory.call_args_list] == [
        ACCOUNT.login_customer_id,
        None,
    ]
    manager.transport.close.assert_called_once()
    direct.transport.close.assert_called_once()
    assert not hasattr(provider._request, "cache")


def test_revocation_unknown_keeps_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auth.CredentialStore, "read", lambda _: {"credentials": {"refresh_token": "fake"}}
    )
    deleted = []
    monkeypatch.setattr(auth.CredentialStore, "delete", lambda _: deleted.append(True))
    monkeypatch.setattr(
        auth.requests, "post", Mock(side_effect=auth.requests.Timeout("token=fake"))
    )
    with pytest.raises(AdsError) as exc:
        auth.disconnect(tmp_path, settings(), revoke=True)
    assert exc.value.code == "REVOCATION_UNKNOWN"
    assert not deleted


@pytest.mark.parametrize(
    "kind,enum,expected",
    [
        ("authorization_error", "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION", "GOOGLE_REJECTED"),
        ("authorization_error", "USER_PERMISSION_DENIED", "GOOGLE_REJECTED"),
        ("internal_error", "INTERNAL_ERROR", "TRANSPORT_UNKNOWN"),
    ],
)
def test_real_sdk_google_error_codes_are_sanitized(kind, enum, expected):
    from google.ads.googleads.errors import GoogleAdsException

    sdk = FakeProvider().sdk
    failure = type(sdk.get_type("GoogleAdsFailure"))(
        errors=[{"error_code": {kind: enum}, "message": "secret=HIDDEN"}]
    )
    error = GoogleAdsException(Mock(), Mock(), failure, "test-request-id")
    result = safe_error(error).public()
    assert result["code"] == expected
    assert result["request_id"] == "test-request-id"
    assert "HIDDEN" not in json.dumps(result)
