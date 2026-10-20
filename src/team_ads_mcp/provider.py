from __future__ import annotations

from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import local
from time import sleep

from google.ads.googleads.client import GoogleAdsClient

from .auth import credentials
from .config import API_VERSION, Settings
from .errors import AdsError, safe_error
from .models import Account, canonical


def utc_now():
    return datetime.now(UTC).isoformat()


def literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def to_dict(message):
    return type(message).to_dict(
        message,
        use_integers_for_enums=False,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,
    )


def read_only_retry(method):
    """Retry a complete read, never mutation RPCs (including validate_only)."""

    @wraps(method)
    def wrapped(*args, **kwargs):
        for attempt in range(3):
            try:
                return method(*args, **kwargs)
            except AdsError as error:
                if error.code != "TRANSPORT_UNKNOWN":
                    raise
                if attempt == 2:
                    raise AdsError(
                        "READ_UNAVAILABLE",
                        "只读请求三次连接尝试失败；本次读取不会修改广告，可稍后重新读取",
                        retryable=True,
                        request_id=error.request_id,
                    ) from None
                sleep(0.5 * (attempt + 1))

    return wrapped


class GoogleProvider:
    source = "google_ads_api"

    def __init__(self, path: Path, settings: Settings):
        self.path, self.settings = path, settings
        self._request = local()

    @contextmanager
    def request_scope(self):
        """Reuse warmed channels within one call; reload credentials on the next call."""
        if getattr(self._request, "cache", None) is not None:
            yield
            return
        cache = {"clients": {}, "services": {}}
        self._request.cache = cache
        try:
            yield
        finally:
            del self._request.cache
            for service in cache["services"].values():
                # Cleanup must never replace a received write result with a close error.
                with suppress(Exception):
                    service.transport.close()

    def client(self, account: Account | None = None):
        login_id = account.login_customer_id if account else None
        cache = getattr(self._request, "cache", None)
        if cache is not None and login_id in cache["clients"]:
            return cache["clients"][login_id]
        client = GoogleAdsClient(
            credentials=credentials(self.path, self.settings),
            version=API_VERSION,
            login_customer_id=login_id,
            use_proto_plus=True,
        )
        if cache is not None:
            cache["clients"][login_id] = client
        return client

    def service(self, name: str, account: Account | None = None):
        key = (name, account.login_customer_id if account else None)
        cache = getattr(self._request, "cache", None)
        if cache is not None and key in cache["services"]:
            return cache["services"][key]
        service = self.client(account).get_service(name)
        if cache is not None:
            cache["services"][key] = service
        return service

    @read_only_retry
    def discover(self):
        try:
            response = self.service("CustomerService").list_accessible_customers(
                retry=None, timeout=self.settings.timeout_seconds
            )
            return {
                "resource_names": list(response.resource_names),
                "scope": "direct_access_only",
                "note": "MCC 子账户需要 account_hierarchy；发现账户不会自动允许读取或写入",
            }
        except Exception as exc:
            raise safe_error(exc) from None

    @read_only_retry
    def search(self, account: Account, query: str, limit: int = 1000):
        try:
            rows = self.service("GoogleAdsService", account).search(
                customer_id=account.customer_id,
                query=query,
                retry=None,
                timeout=self.settings.timeout_seconds,
            )
            result = []
            for row in rows:
                if len(result) >= limit:
                    raise AdsError(
                        "RESULT_LIMIT", "结果超过限制，请缩小查询范围；没有把截断数据当成完整结果"
                    )
                result.append(to_dict(row))
            return result
        except Exception as exc:
            raise safe_error(exc) from None

    def account(self, account: Account):
        rows = self.search(
            account,
            "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
            "customer.time_zone, customer.manager, customer.test_account, customer.status "
            "FROM customer LIMIT 1",
        )
        if not rows:
            raise AdsError("ACCOUNT_UNAVAILABLE", "未返回账户信息")
        return rows[0]["customer"]

    @read_only_retry
    def fields(self, name: str):
        if not name or not all(c.isalnum() or c in "_." for c in name):
            raise AdsError("INVALID_FIELD", "请输入资源或字段名")
        try:
            service = self.service("GoogleAdsFieldService")
            # GAQL supports AND, but not OR. Escape '_' in the prefix LIKE pattern.
            prefix = name.replace("_", "[_]") + ".%"
            rows = []
            for condition in [f"name = {literal(name)}", f"name LIKE {literal(prefix)}"]:
                query = (
                    "SELECT name, category, data_type, selectable, filterable, sortable, "
                    f"selectable_with, attribute_resources, metrics, segments WHERE {condition}"
                )
                result = service.search_google_ads_fields(
                    query=query, retry=None, timeout=self.settings.timeout_seconds
                )
                for row in result:
                    if len(rows) >= 500:
                        raise AdsError("RESULT_LIMIT", "字段超过限制，请指定更具体的字段名")
                    rows.append(to_dict(row))
            return rows
        except Exception as exc:
            raise safe_error(exc) from None

    @read_only_retry
    def keyword_ideas(
        self, account: Account, seeds: list[str], geo_ids: list[str], language_id: str
    ):
        try:
            response = self.service("KeywordPlanIdeaService", account).generate_keyword_ideas(
                request={
                    "customer_id": account.customer_id,
                    "language": f"languageConstants/{language_id}",
                    "geo_target_constants": [f"geoTargetConstants/{x}" for x in geo_ids],
                    "keyword_plan_network": "GOOGLE_SEARCH",
                    "include_adult_keywords": False,
                    "keyword_seed": {"keywords": seeds},
                    "page_size": 100,
                },
                retry=None,
                timeout=self.settings.timeout_seconds,
            )
            rows = []
            truncated = False
            for row in response:
                if len(rows) == 100:
                    truncated = True
                    break
                rows.append(to_dict(row))
            return {
                "ideas": rows,
                "truncated": truncated,
                "historical_window": "Google 返回的 monthly_search_volumes",
                "note": "估算/历史聚合，不代表账户真实转化或未来效果；缺字段不填 0",
            }
        except Exception as exc:
            raise safe_error(exc) from None

    def mutate(self, account: Account, operations: list[dict], validate_only: bool):
        try:
            client = self.client(account)
            request = type(client.get_type("MutateGoogleAdsRequest"))(
                customer_id=account.customer_id,
                mutate_operations=operations,
                partial_failure=False,
                validate_only=validate_only,
                response_content_type="RESOURCE_NAME_ONLY",
            )
            response = self.service("GoogleAdsService", account).mutate(
                request=request, retry=None, timeout=self.settings.timeout_seconds
            )
            result = to_dict(response)
            if result.get("partial_failure_error", {}).get("code", 0):
                raise AdsError(
                    "PARTIAL_FAILURE", "响应包含部分失败，不能作为完整成功；需要远端核对"
                )
            if not validate_only and len(result.get("mutate_operation_responses", [])) != len(
                operations
            ):
                raise AdsError(
                    "INCOMPLETE_RECEIPT", "Google 回执数量与计划操作数量不一致，必须回查"
                )
            return result
        except Exception as exc:
            raise safe_error(exc) from None


def query_rows(provider, account, resource, fields, condition=""):
    query = f"SELECT {', '.join(fields)} FROM {resource}"
    if condition:
        query += " WHERE " + condition
    return sorted(
        (project_row(row, fields) for row in provider.search(account, query)), key=canonical
    )


def project_row(row, fields):
    """Discard unselected protobuf defaults; they are not remote observations."""
    result = {}
    for field in fields:
        keys = ["type_" if part == "type" else part for part in field.split(".")]
        value = row
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            node = result
            for key in keys[:-1]:
                node = node.setdefault(key, {})
            node[keys[-1]] = value
    return result


def one(provider, account, resource, fields, resource_name):
    rows = query_rows(
        provider, account, resource, fields, f"{resource}.resource_name = {literal(resource_name)}"
    )
    if len(rows) != 1:
        raise AdsError("RESOURCE_UNAVAILABLE", "资源不存在、已移除或不可访问")
    return rows[0]
