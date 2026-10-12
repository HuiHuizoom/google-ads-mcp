"""Strict, deliberately bounded Search campaign contracts (no raw mutations)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CustomerId = Annotated[str, StringConstraints(pattern=r"^[0-9]{10}$")]
PositiveId = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
Money = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,9})(\.[0-9]{1,6})?$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
CampaignRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/campaigns/[1-9][0-9]*$")
]
GroupRef = Annotated[str, StringConstraints(pattern=r"^customers/[0-9]{10}/adGroups/[1-9][0-9]*$")]
BudgetRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/campaignBudgets/[1-9][0-9]*$")
]
CriterionRef = Annotated[
    str,
    StringConstraints(
        pattern=r"^customers/[0-9]{10}/(adGroupCriteria|campaignCriteria)/[1-9][0-9]*~[1-9][0-9]*$"
    ),
]
StatusRef = Annotated[
    str,
    StringConstraints(
        pattern=r"^customers/[0-9]{10}/(campaigns/[1-9][0-9]*|adGroups/[1-9][0-9]*|adGroupAds/[1-9][0-9]*~[1-9][0-9]*)$"
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Account(StrictModel):
    customer_id: CustomerId
    login_customer_id: CustomerId | None = None


def micros(value: str) -> int:
    result = Decimal(value) * 1_000_000
    if not result.is_finite() or result <= 0 or result != result.to_integral_value():
        raise ValueError("金额必须是正数，且最多 6 位小数；使用账户本币的字符串")
    if result > 9_223_372_036_854_775_807:
        raise ValueError("金额超出 int64")
    return int(result)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Keyword(StrictModel):
    text: str = Field(min_length=1, max_length=80)
    match_type: Literal["EXACT", "PHRASE", "BROAD"]

    @field_validator("text")
    @classmethod
    def clean(cls, value: str) -> str:
        if value != value.strip() or not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("关键词不能含首尾空格或控制字符")
        return value


class AddKeywords(StrictModel):
    kind: Literal["add_keywords"]
    ad_group: GroupRef
    keywords: list[Keyword] = Field(min_length=1, max_length=100)


class AddNegatives(StrictModel):
    kind: Literal["add_negatives"]
    campaign: CampaignRef
    keywords: list[Keyword] = Field(min_length=1, max_length=100)


class RemoveKeywords(StrictModel):
    kind: Literal["remove_keywords"]
    resources: list[CriterionRef] = Field(min_length=1, max_length=100)


class SetStatus(StrictModel):
    kind: Literal["set_status"]
    resource: StatusRef
    status: Literal["PAUSED", "ENABLED"]


class SetBudget(StrictModel):
    kind: Literal["set_budget"]
    budget: BudgetRef
    currency: Currency
    daily_amount: Money
    # Exact membership is required, even for a non-shared budget.
    expected_campaigns: list[CampaignRef] = Field(min_length=1, max_length=100)

    @field_validator("daily_amount")
    @classmethod
    def positive(cls, value: str) -> str:
        micros(value)
        return value


class SearchGroup(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    cpc_bid: Money
    keywords: list[Keyword] = Field(min_length=1, max_length=100)
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)
    final_url: str = Field(max_length=2048, pattern=r"^https://[^\s]+$")

    @model_validator(mode="after")
    def validate_copy(self):
        micros(self.cpc_bid)
        from urllib.parse import urlsplit

        url = urlsplit(self.final_url)
        if not url.hostname or url.username or url.password:
            raise ValueError("final_url 必须是有效 HTTPS 地址且不包含凭据")
        for values, limit in [(self.headlines, 30), (self.descriptions, 90)]:
            if len(set(values)) != len(values):
                raise ValueError("广告文案不得重复")
            for text in values:
                width = sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)
                if not text.strip() or width > limit or any(ord(c) < 32 for c in text):
                    raise ValueError(f"文案超出 {limit} 字符单位或含控制字符；全角字符计 2")
        return self


class CreateSearch(StrictModel):
    kind: Literal["create_search"]
    name: str = Field(min_length=1, max_length=80)
    currency: Currency
    daily_amount: Money
    # Manual CPC is explicit, deterministic and independent of conversion setup.
    bidding: Literal["MANUAL_CPC"] = "MANUAL_CPC"
    geo_target_ids: list[PositiveId] = Field(min_length=1, max_length=20)
    language_ids: list[PositiveId] = Field(min_length=1, max_length=10)
    eu_political_advertising: Literal["DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"]
    ad_groups: list[SearchGroup] = Field(min_length=1, max_length=5)
    negative_keywords: list[Keyword] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        micros(self.daily_amount)
        for values in [self.geo_target_ids, self.language_ids, [g.name for g in self.ad_groups]]:
            if len(set(values)) != len(values):
                raise ValueError("地域、语言和广告组名称不得重复")
        return self


Change = Annotated[
    AddKeywords | AddNegatives | RemoveKeywords | SetStatus | SetBudget | CreateSearch,
    Field(discriminator="kind"),
]


class PlanInput(StrictModel):
    account: Account
    change: Change
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def account_isolation(self):
        for customer in re.findall(r"customers/([0-9]+)/", canonical(self.change.model_dump())):
            if customer != self.account.customer_id:
                raise ValueError("资源属于其他账户")
        keywords = getattr(self.change, "keywords", [])
        groups = getattr(self.change, "ad_groups", [])
        for values in [
            keywords,
            getattr(self.change, "negative_keywords", []),
            *[g.keywords for g in groups],
        ]:
            keys = [(k.text.casefold(), k.match_type) for k in values]
            if len(keys) != len(set(keys)):
                raise ValueError("重复关键词")
        if isinstance(self.change, RemoveKeywords) and len(set(self.change.resources)) != len(
            self.change.resources
        ):
            raise ValueError("重复资源")
        return self


class DateRange(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def valid(self):
        if self.start > self.end or (self.end - self.start).days > 366:
            raise ValueError("日期范围须为顺序明确的最多 367 个自然日")
        return self
