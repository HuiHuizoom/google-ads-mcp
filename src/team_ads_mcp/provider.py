from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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


class GoogleProvider:
    source = "google_ads_api"

    def __init__(self, path: Path, settings: Settings):
        self.path, self.settings = path, settings

    def client(self, account: Account | None = None):
        return GoogleAdsClient(
            credentials=credentials(self.path, self.settings),
            version=API_VERSION,
            login_customer_id=account.login_customer_id if account else None,
            use_proto_plus=True,
        )

    def discover(self):
        try:
            response = (
                self.client()
                .get_service("CustomerService")
                .list_accessible_customers(retry=None, timeout=self.settings.timeout_seconds)
            )
            return {
                "resource_names": list(response.resource_names),
                "scope": "direct_access_only",
                "note": "MCC 子账户需要 account_hierarchy；发现账户不会自动允许读取或写入",
            }
        except Exception as exc:
            raise safe_error(exc) from None

    def search(self, account: Account, query: str, limit: int = 1000):
        try:
            rows = (
                self.client(account)
                .get_service("GoogleAdsService")
                .search(
                    customer_id=account.customer_id,
                    query=query,
                    retry=None,
                    timeout=self.settings.timeout_seconds,
                )
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

    def fields(self, name: str):
        if not name or not all(c.isalnum() or c in "_." for c in name):
            raise AdsError("INVALID_FIELD", "请输入资源或字段名")
        try:
            query = (
                "SELECT name, category, data_type, selectable, filterable, sortable, "
                "selectable_with, attribute_resources, metrics, segments "
                f"WHERE name = {literal(name)} OR name LIKE {literal(name + '.%')} LIMIT 500"
            )
            result = (
                self.client()
                .get_service("GoogleAdsFieldService")
                .search_google_ads_fields(
                    query=query, retry=None, timeout=self.settings.timeout_seconds
                )
            )
            return [to_dict(row) for row in result]
        except Exception as exc:
            raise safe_error(exc) from None

    def keyword_ideas(
        self, account: Account, seeds: list[str], geo_ids: list[str], language_id: str
    ):
        try:
            client = self.client(account)
            response = client.get_service("KeywordPlanIdeaService").generate_keyword_ideas(
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
            response = client.get_service("GoogleAdsService").mutate(
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
    return sorted(provider.search(account, query), key=canonical)


def one(provider, account, resource, fields, resource_name):
    rows = query_rows(
        provider, account, resource, fields, f"{resource}.resource_name = {literal(resource_name)}"
    )
    if len(rows) != 1:
        raise AdsError("RESOURCE_UNAVAILABLE", "资源不存在、已移除或不可访问")
    return rows[0]
