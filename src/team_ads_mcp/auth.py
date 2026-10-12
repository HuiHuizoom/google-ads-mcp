"""Credentials stay in the OS credential store, outside tools and the ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import google.auth
import keyring
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import ADS_SCOPE, Settings, save_settings
from .errors import AdsError, safe_error


class CredentialStore:
    def __init__(self, path: Path):
        self.service = (
            "team-ads-mcp:" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
        )

    def _backend(self):
        backend = keyring.get_keyring()
        # Never silently fall back to an unencrypted file backend.
        module = type(backend).__module__
        if module not in {
            "keyring.backends.macOS",
            "keyring.backends.SecretService",
            "keyring.backends.Windows",
        }:
            raise AdsError(
                "SECURE_STORE_UNAVAILABLE",
                "需要 macOS Keychain、Secret Service 或 Windows Credential Store",
            )

    def read(self):
        self._backend()
        raw = keyring.get_password(self.service, "google-oauth")
        if not raw:
            raise AdsError(
                "AUTH_REQUIRED",
                "运行 team-ads auth login --client-file <本机文件路径> 完成 Google 授权",
            )
        return json.loads(raw)

    def write(self, record):
        self._backend()
        keyring.set_password(self.service, "google-oauth", json.dumps(record))

    def delete(self):
        self._backend()
        try:
            keyring.delete_password(self.service, "google-oauth")
        except keyring.errors.PasswordDeleteError:
            pass


def load_client_config(client_file: Path, settings: Settings) -> dict:
    data = json.loads(client_file.read_text())
    installed = data.get("installed")
    if not installed or installed.get("project_id") != settings.cloud_project:
        raise AdsError("OAUTH_PROJECT_MISMATCH", "需要配置项目所属的 Desktop OAuth 客户端 JSON")
    if (
        installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
        or installed.get("token_uri") != "https://oauth2.googleapis.com/token"
    ):
        raise AdsError("OAUTH_ENDPOINT_REJECTED", "OAuth 客户端必须使用 Google 官方端点")
    return data


def login(path: Path, settings: Settings, client_file: Path):
    config = load_client_config(client_file, settings)
    store = CredentialStore(path)
    store._backend()  # Fail before opening the authorization screen if storage is unavailable.
    flow = InstalledAppFlow.from_client_config(config, [ADS_SCOPE], autogenerate_code_verifier=True)
    credentials = flow.run_local_server(
        host="127.0.0.1",
        bind_addr="127.0.0.1",
        port=0,
        timeout_seconds=180,
        authorization_prompt_message="浏览器已打开 Google 授权；请在 3 分钟内完成。",
        success_message="Google 授权已收到。可以关闭此页面，回到终端查看保存结果。",
        access_type="offline",
        prompt="consent",
    )
    if not credentials.refresh_token:
        raise AdsError("NO_REFRESH_TOKEN", "未取得离线授权，请重新登录并完成 consent")
    store.write(
        {"credentials": json.loads(credentials.to_json()), "cloud_project": settings.cloud_project}
    )
    settings.auth_mode = "oauth"
    settings.oauth_authorization_recorded = True
    settings.auth_epoch = uuid4().hex
    save_settings(path, settings)
    return {
        "authorized": True,
        "cloud_project": settings.cloud_project,
        "stored_in": "OS credential store",
        "google_ads_access_verified": False,
    }


def credentials(path: Path, settings: Settings):
    try:
        if settings.auth_mode == "adc":
            creds, project = google.auth.default(scopes=[ADS_SCOPE])
            # project may be absent for user ADC; quota project does not prove OAuth ownership.
            if project and project != settings.cloud_project:
                raise AdsError("ADC_PROJECT_MISMATCH", "ADC 项目与本地配置不同，请核对凭据所属项目")
            if not creds.valid:
                creds.refresh(Request())
            return creds
        store = CredentialStore(path)
        record = store.read()
        if record["cloud_project"] != settings.cloud_project:
            raise AdsError("OAUTH_PROJECT_MISMATCH", "已保存授权与 Cloud 项目不匹配，需要重新登录")
        creds = Credentials.from_authorized_user_info(record["credentials"], scopes=[ADS_SCOPE])
        if not creds.valid:
            creds.refresh(Request())
            store.write(
                {
                    "credentials": json.loads(creds.to_json()),
                    "cloud_project": settings.cloud_project,
                }
            )
        return creds
    except Exception as exc:
        raise safe_error(exc) from None


def disconnect(path: Path, settings: Settings, revoke: bool):
    if settings.auth_mode != "oauth":
        raise AdsError(
            "ADC_EXTERNALLY_MANAGED", "ADC 由外部管理；使用其原授权工具撤销，不会删除其他应用的 ADC"
        )
    store = CredentialStore(path)
    if revoke:
        record = store.read()
        try:
            response = requests.post(
                "https://oauth2.googleapis.com/revoke",
                data={"token": record["credentials"]["refresh_token"]},
                timeout=20,
            )
        except requests.RequestException:
            raise AdsError(
                "REVOCATION_UNKNOWN", "撤销结果不明，保留本地记录；可在 Google 账户权限页核对"
            ) from None
        if response.status_code != 200:
            raise AdsError(
                "REVOCATION_UNCONFIRMED", "Google 未确认撤销；可先在 Google 账户权限页检查"
            )
    store.delete()
    settings.oauth_authorization_recorded = False
    settings.auth_epoch = uuid4().hex
    save_settings(path, settings)
    return {"local_credentials_removed": True, "remote_revocation_confirmed": revoke}
