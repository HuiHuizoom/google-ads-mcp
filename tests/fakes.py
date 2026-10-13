"""Offline stateful simulator, for tests only. It is NOT evidence of Google acceptance."""

import re
from copy import deepcopy

from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from team_ads_mcp.config import AccountPolicy, Settings
from team_ads_mcp.models import Account
from team_ads_mcp.provider import to_dict

ACCOUNT = Account(customer_id="1234567890", login_customer_id="9876543210")
ROOT = "customers/1234567890"
CAMPAIGN = ROOT + "/campaigns/11"
BUDGET = ROOT + "/campaignBudgets/21"
GROUP = ROOT + "/adGroups/31"


def settings():
    return Settings(
        cloud_project="test-project", accounts=[AccountPolicy(account=ACCOUNT, writes=True)]
    )


def get_path(item, path):
    for part in path.split("."):
        if not isinstance(item, dict):
            return None
        item = item.get("type_" if part == "type" else part)
    return item


def put_path(item, path, value):
    keys = path.split(".")
    for key in keys[:-1]:
        item = item.setdefault(key, {})
    item["type_" if keys[-1] == "type" else keys[-1]] = deepcopy(value)


def clear_path(item, path):
    keys = path.split(".")
    for key in keys[:-1]:
        item = item.setdefault(key, {})
    item.pop(keys[-1], None)


class FakeProvider:
    source = "offline_test_simulator"

    def __init__(self):
        self.metadata = {
            "id": "1234567890",
            "descriptive_name": "Offline Test Account",
            "currency_code": "USD",
            "time_zone": "America/New_York",
            "manager": False,
            "test_account": True,
            "status": "ENABLED",
        }
        self.records = {
            "campaign_budget": {
                BUDGET: {
                    "resource_name": BUDGET,
                    "name": "Existing budget",
                    "amount_micros": 20_000_000,
                    "period": "DAILY",
                    "status": "ENABLED",
                    "explicitly_shared": False,
                    "reference_count": 1,
                }
            },
            "campaign": {
                CAMPAIGN: {
                    "resource_name": CAMPAIGN,
                    "name": "Existing",
                    "status": "PAUSED",
                    "campaign_budget": BUDGET,
                    "advertising_channel_type": "SEARCH",
                    "bidding_strategy_type": "MANUAL_CPC",
                }
            },
            "ad_group": {
                GROUP: {
                    "resource_name": GROUP,
                    "name": "Existing group",
                    "status": "PAUSED",
                    "campaign": CAMPAIGN,
                    "cpc_bid_micros": 1_000_000,
                    "type_": "SEARCH_STANDARD",
                }
            },
            "ad_group_ad": {},
            "ad_group_criterion": {},
            "campaign_criterion": {},
            "asset": {},
            "campaign_asset": {},
            "ad_group_criterion_label": {},
        }
        self.calls = []
        self.sdk = GoogleAdsClient(
            credentials=AnonymousCredentials(), version="v25", use_proto_plus=True
        )
        self.failure = None
        self.apply_before_failure = False
        self.validation_failure = None
        self.counter = 100

    def account(self, account):
        assert account == ACCOUNT
        return deepcopy(self.metadata)

    def discover(self):
        return {"resource_names": ["customers/9876543210"], "scope": "direct_access_only"}

    def joined(self, resource, body):
        row = {resource: deepcopy(body)}
        if resource in {"ad_group_ad", "ad_group_criterion"}:
            row["ad_group"] = deepcopy(self.records["ad_group"][body["ad_group"]])
        if resource in {"campaign_criterion", "campaign_asset"}:
            row["campaign"] = deepcopy(self.records["campaign"][body["campaign"]])
        if resource == "campaign_asset":
            row["asset"] = deepcopy(self.records["asset"][body["asset"]])
        if "ad_group" in row:
            row["campaign"] = deepcopy(self.records["campaign"][row["ad_group"]["campaign"]])
        if "campaign" in row:
            row["campaign_budget"] = deepcopy(
                self.records["campaign_budget"][row["campaign"]["campaign_budget"]]
            )
        return row

    def search(self, account, query, limit=1000):
        assert account == ACCOUNT
        resource = re.search(r" FROM ([a-z_]+)", query).group(1)
        if resource == "customer":
            return [{"customer": self.account(account)}]
        if resource not in self.records:
            return []
        fields = query.split("SELECT ", 1)[1].split(" FROM ")[0].split(",")
        where = query.split(" WHERE ", 1)[1] if " WHERE " in query else ""
        where = re.split(r" ORDER BY | LIMIT ", where)[0]
        conditions = re.findall(r"([a-z_.]+)\s*(!=|=)\s*'((?:\\.|[^'])*)'", where)
        result = []
        for body in self.records[resource].values():
            row = self.joined(resource, body)
            passed = True
            for path, operator, expected in conditions:
                expected = expected.replace("\\'", "'").replace("\\\\", "\\")
                equal = str(get_path(row, path)) == expected
                passed &= equal if operator == "=" else not equal
            if passed:
                projected = {}
                for field in fields:
                    value = get_path(row, field.strip())
                    if value is not None:
                        put_path(projected, field.strip(), value)
                result.append(to_dict(type(self.sdk.get_type("GoogleAdsRow"))(projected)))
        return deepcopy(result)

    def mutate(self, account, operations, validate_only):
        assert account == ACCOUNT
        # Test requests against the real, locked SDK message schema, not just dictionaries.
        req = type(self.sdk.get_type("MutateGoogleAdsRequest"))(
            customer_id=account.customer_id,
            mutate_operations=operations,
            partial_failure=False,
            validate_only=validate_only,
        )
        type(req).serialize(req)
        # FieldMask strings are not validated by protobuf construction itself.
        for entry in req.mutate_operations:
            pb_entry = type(entry).pb(entry)
            op = getattr(pb_entry, pb_entry.WhichOneof("operation"))
            if op.WhichOneof("operation") == "update":
                for path in op.update_mask.paths:
                    descriptor = op.update.DESCRIPTOR
                    for part in path.split("."):
                        assert part in descriptor.fields_by_name, path
                        field = descriptor.fields_by_name[part]
                        descriptor = field.message_type
                    assert field.message_type is None or field.is_repeated, (
                        f"mask must address a leaf or repeated field: {path}"
                    )
        self.calls.append((validate_only, deepcopy(operations)))
        if validate_only:
            if self.validation_failure:
                raise self.validation_failure
            return {}
        if self.failure and not self.apply_before_failure:
            raise self.failure
        mapping, names = {}, []
        collections = {
            "campaign_budget": "campaignBudgets",
            "campaign": "campaigns",
            "ad_group": "adGroups",
            "ad_group_ad": "adGroupAds",
            "ad_group_criterion": "adGroupCriteria",
            "campaign_criterion": "campaignCriteria",
            "asset": "assets",
            "campaign_asset": "campaignAssets",
            "ad_group_criterion_label": "adGroupCriterionLabels",
        }

        def substitute(value):
            if isinstance(value, dict):
                return {k: substitute(v) for k, v in value.items()}
            if isinstance(value, list):
                return [substitute(v) for v in value]
            return mapping.get(value, value) if isinstance(value, str) else value

        for op in operations:
            key = next(iter(op))
            resource, value = key.removesuffix("_operation"), op[key]
            if "create" in value:
                self.counter += 1
                body = substitute(deepcopy(value["create"]))
                suffix = str(self.counter)
                if resource in {"ad_group_ad", "ad_group_criterion", "campaign_criterion"}:
                    parent = body.get("ad_group", body.get("campaign"))
                    suffix = parent.rsplit("/", 1)[1] + "~" + suffix
                name = ROOT + "/" + collections[resource] + "/" + suffix
                if "resource_name" in body:
                    mapping[body["resource_name"]] = name
                body["resource_name"] = name
                if resource not in {"asset", "ad_group_criterion_label"}:
                    body.setdefault("status", "ENABLED")
                if resource == "campaign_budget":
                    body.update(period="DAILY", reference_count=1)
                if resource == "campaign":
                    body["bidding_strategy_type"] = "MANUAL_CPC"
                if resource.endswith("criterion") and "keyword" in body:
                    body["type_"] = "KEYWORD"
                if resource == "campaign_criterion":
                    for field, kind in [
                        ("location", "LOCATION"),
                        ("language", "LANGUAGE"),
                        ("ad_schedule", "AD_SCHEDULE"),
                        ("device", "DEVICE"),
                    ]:
                        if field in body:
                            body["type_"] = kind
                if resource == "ad_group_ad":
                    body["ad"].update(
                        resource_name=ROOT + "/ads/" + str(self.counter),
                        type_="RESPONSIVE_SEARCH_AD",
                    )
                if resource in {"asset", "campaign_asset"}:
                    body["source"] = "ADVERTISER"
                if resource == "asset":
                    body["type_"] = next(
                        k.removesuffix("_asset").upper() for k in body if k.endswith("_asset")
                    )
                if resource == "ad_group_criterion_label":
                    criterion = self.records["ad_group_criterion"][body["ad_group_criterion"]]
                    criterion.setdefault("labels", []).append(body["label"])
                self.records[resource][name] = body
            elif "update" in value:
                body = value["update"]
                name = body["resource_name"]
                targets = (
                    [
                        r["ad"]
                        for r in self.records["ad_group_ad"].values()
                        if r["ad"]["resource_name"] == name
                    ]
                    if resource == "ad"
                    else [self.records[resource][name]]
                )
                for target in targets:
                    for path in value["update_mask"]["paths"]:
                        updated = get_path(body, path)
                        if updated is None:
                            clear_path(target, path)
                        else:
                            put_path(target, path, updated)
                    if resource == "campaign":
                        strategies = {
                            "manual_cpc": "MANUAL_CPC",
                            "target_spend": "TARGET_SPEND",
                            "maximize_conversions": "MAXIMIZE_CONVERSIONS",
                            "maximize_conversion_value": "MAXIMIZE_CONVERSION_VALUE",
                        }
                        chosen = next((k for k in body if k in strategies), None)
                        if chosen:
                            target["bidding_strategy_type"] = strategies[chosen]
                            target.setdefault(chosen, {})
                            for other in strategies.keys() - {chosen}:
                                target.pop(other, None)
            else:
                name = value["remove"]
                self.records[resource][name]["status"] = "REMOVED"
            names.append(name)
        if self.failure:
            raise self.failure
        return {"mutate_operation_responses": [{"resource_name": name} for name in names]}


def search_input(key="new-search-001"):
    return {
        "account": ACCOUNT.model_dump(),
        "request_key": key,
        "change": {
            "kind": "create_search",
            "name": "Example Search",
            "currency": "USD",
            "daily_amount": "25.000001",
            "geo_target_ids": ["2840"],
            "language_ids": ["1000"],
            "eu_political_advertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
            "ad_groups": [
                {
                    "name": "Intent",
                    "cpc_bid": "1.20",
                    "final_url": "https://example.com",
                    "headlines": ["Example Product", "Explore Features", "Try Our Service"],
                    "descriptions": [
                        "Find the right product for your team.",
                        "Compare features and get started today.",
                    ],
                    "keywords": [{"text": "team product", "match_type": "EXACT"}],
                }
            ],
            "negative_keywords": [{"text": "free", "match_type": "PHRASE"}],
        },
    }
