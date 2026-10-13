"""Strict, deliberately bounded Search campaign contracts (no raw mutations)."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import unicodedata
from datetime import date, datetime
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


AdRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/adGroupAds/[1-9][0-9]*~[1-9][0-9]*$")
]
GroupCriterionRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/adGroupCriteria/[1-9][0-9]*~[1-9][0-9]*$")
]
CampaignCriterionRef = Annotated[
    str,
    StringConstraints(pattern=r"^customers/[0-9]{10}/campaignCriteria/[1-9][0-9]*~[1-9][0-9]*$"),
]


def validate_text(value: str, width: int):
    if not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("文字不能为空或包含控制字符")
    if sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in value) > width:
        raise ValueError(f"文字超过 {width} 字符单位；全角字符计 2")
    return value


def validate_urls(values: list[str]):
    from urllib.parse import urlsplit

    for value in values:
        parsed = urlsplit(value)
        if (
            len(value) > 2084
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError("网址必须是有效 HTTPS 地址且不包含凭据或空白")
    return values


class Patch(StrictModel):
    @model_validator(mode="after")
    def supplied_fields(self):
        if not self.model_fields_set or any(
            getattr(self, k) is None for k in self.model_fields_set
        ):
            raise ValueError("至少设置一个字段；省略表示保持原值，不接受 null")
        return self


class RSAPatch(Patch):
    """省略字段保留原值；标题/描述/网址列表整体替换，提供标题/描述时取消该列表旧固定位置。path1/path2 可用空字符串清除。"""

    headlines: list[str] | None = Field(default=None, min_length=3, max_length=15)
    descriptions: list[str] | None = Field(default=None, min_length=2, max_length=4)
    final_urls: list[str] | None = Field(default=None, min_length=1, max_length=10)
    path1: str | None = Field(default=None, max_length=15)
    path2: str | None = Field(default=None, max_length=15)

    @model_validator(mode="after")
    def copy_valid(self):
        for values, limit in [(self.headlines, 30), (self.descriptions, 90)]:
            if values is not None:
                if len(set(values)) != len(values):
                    raise ValueError("广告文案不得重复")
                for value in values:
                    validate_text(value, limit)
        if self.final_urls is not None:
            validate_urls(self.final_urls)
        for value in [self.path1, self.path2]:
            if value:
                validate_text(value, 15)
                if "/" in value or any(c.isspace() for c in value):
                    raise ValueError("显示路径不能含斜杠或空白")
        return self


class RSAContent(RSAPatch):
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)
    final_urls: list[str] = Field(min_length=1, max_length=10)


class CreateAdGroup(StrictModel):
    """向已有 Search 系列新增 PAUSED 广告组；手动 CPC 系列必须提供 cpc_bid。可同时添加关键词和最多 3 条 PAUSED RSA。"""

    kind: Literal["create_ad_group"]
    campaign: CampaignRef
    name: str = Field(min_length=1, max_length=100)
    currency: Currency
    cpc_bid: Money | None = None
    keywords: list[Keyword] = Field(default_factory=list, max_length=100)
    ads: list[RSAContent] = Field(default_factory=list, max_length=3)


class AdGroupPatch(Patch):
    """只修改提供的名称或 CPC；cpc_bid 为账户本币十进制字符串，仅用于标准手动 CPC。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    cpc_bid: Money | None = None


class UpdateAdGroup(StrictModel):
    kind: Literal["update_ad_group"]
    ad_group: GroupRef
    currency: Currency
    patch: AdGroupPatch


class CreateAd(StrictModel):
    """向已有广告组添加一条 PAUSED 响应式搜索广告；现有广告不变。"""

    kind: Literal["create_ad"]
    ad_group: GroupRef
    ad: RSAContent


class UpdateAd(StrictModel):
    """编辑已有 RSA。先用 list_resources(resource='ads', ad_resource=广告的 ad.resource_name) 查询全部未移除关联，完整填入 expected_ad_group_ads。修改会影响这些关联。"""

    kind: Literal["update_ad"]
    ad_group_ad: AdRef
    patch: RSAPatch
    expected_ad_group_ads: list[AdRef] = Field(min_length=1, max_length=100)


class KeywordPatch(Patch):
    """省略保留；clear_cpc_bid=true 清除关键词单独出价以继承广告组出价；final_urls=[] 清除关键词专属网址。仅正向关键词支持此操作。"""

    status: Literal["PAUSED", "ENABLED"] | None = None
    cpc_bid: Money | None = None
    clear_cpc_bid: Literal[True] | None = None
    final_urls: list[str] | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def valid(self):
        if self.cpc_bid is not None and self.clear_cpc_bid:
            raise ValueError("不能同时设置及清除关键词出价")
        if self.final_urls is not None:
            validate_urls(self.final_urls)
        return self


class UpdateKeyword(StrictModel):
    kind: Literal["update_keyword"]
    resource: GroupCriterionRef
    currency: Currency
    patch: KeywordPatch


class ReplaceKeyword(StrictModel):
    """文字/匹配方式不可原地修改：原子移除旧词并新建；保留状态、单独 CPC、网址/跟踪参数及标签，但不继承资源 ID 和历史统计。适用于正向及否定词。"""

    kind: Literal["replace_keyword"]
    resource: CriterionRef
    keyword: Keyword


class AddAdGroupNegatives(StrictModel):
    kind: Literal["add_ad_group_negatives"]
    ad_group: GroupRef
    keywords: list[Keyword] = Field(min_length=1, max_length=100)


class CampaignPatch(Patch):
    """省略保留。日期为账户时区 yyyy-MM-dd HH:mm:ss；已开始的系列不能改开始时间。search_partners 控制搜索合作伙伴，display_network 控制展示网络扩展。final_url_suffix='' 清除后缀。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    start_date_time: str | None = None
    end_date_time: str | None = None
    search_partners: bool | None = None
    display_network: bool | None = None
    positive_geo_target_type: Literal["PRESENCE", "PRESENCE_OR_INTEREST"] | None = None
    negative_geo_target_type: Literal["PRESENCE"] | None = None
    final_url_suffix: str | None = Field(default=None, max_length=2048)

    @field_validator("start_date_time", "end_date_time")
    @classmethod
    def date_time(cls, value):
        if value is not None:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            if parsed.strftime("%Y-%m-%d %H:%M:%S") != value:
                raise ValueError("使用账户时区的 yyyy-MM-dd HH:mm:ss")
        return value


class UpdateCampaign(StrictModel):
    kind: Literal["update_campaign"]
    campaign: CampaignRef
    patch: CampaignPatch


class SetBidding(StrictModel):
    """设置本系列的标准出价策略；如原为组合策略则退出组合。MAXIMIZE_CONVERSIONS 可带 target_cpa，MAXIMIZE_CONVERSION_VALUE 可带 target_roas（2.5=250%），MAXIMIZE_CLICKS 可带 max_cpc。省略可选目标表示清除该目标。"""

    kind: Literal["set_bidding"]
    campaign: CampaignRef
    currency: Currency
    strategy: Literal[
        "MANUAL_CPC", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE"
    ]
    target_cpa: Money | None = None
    target_roas: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    max_cpc: Money | None = None

    @model_validator(mode="after")
    def strategy_fields(self):
        for name, strategy in [
            ("target_cpa", "MAXIMIZE_CONVERSIONS"),
            ("target_roas", "MAXIMIZE_CONVERSION_VALUE"),
            ("max_cpc", "MAXIMIZE_CLICKS"),
        ]:
            value = getattr(self, name)
            if value is not None and self.strategy != strategy:
                raise ValueError(f"{name} 只适用于 {strategy}")
            if name != "target_roas" and value is not None:
                micros(value)
        return self


class TargetingPatch(Patch):
    """每个提供的列表表示该维度的完整目标集合；省略保留，[] 清空。清空正向地区/语言限制可能扩大投放。geo_target_ids 包含地区，excluded_geo_target_ids 排除地区。"""

    geo_target_ids: list[PositiveId] | None = Field(default=None, max_length=100)
    excluded_geo_target_ids: list[PositiveId] | None = Field(default=None, max_length=100)
    language_ids: list[PositiveId] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def unique(self):
        for name in self.model_fields_set:
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError("定位 ID 不得重复")
        if set(self.geo_target_ids or []) & set(self.excluded_geo_target_ids or []):
            raise ValueError("同一地区不能同时包含和排除")
        return self


class SetTargeting(StrictModel):
    kind: Literal["set_targeting"]
    campaign: CampaignRef
    patch: TargetingPatch


class Schedule(StrictModel):
    day: Literal["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
    start: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):(?:00|15|30|45)$")
    end: str = Field(pattern=r"^(?:(?:[01][0-9]|2[0-3]):(?:00|15|30|45)|24:00)$")

    @model_validator(mode="after")
    def order(self):
        if self.start >= self.end:
            raise ValueError("结束必须晚于开始；跨午夜请拆成两天")
        return self


class SetSchedule(StrictModel):
    """按账户时区替换完整一周投放时段；[] 恢复全天。每日至多 6 段、15 分钟粒度、不得重叠，跨午夜拆为两天。新时段采用默认出价系数。"""

    kind: Literal["set_schedule"]
    campaign: CampaignRef
    schedules: list[Schedule] = Field(max_length=42)

    @model_validator(mode="after")
    def overlap(self):
        for day in {s.day for s in self.schedules}:
            items = sorted((s.start, s.end) for s in self.schedules if s.day == day)
            if len(items) > 6 or any(a[1] > b[0] for a, b in itertools.pairwise(items)):
                raise ValueError("每一天最多 6 个时段且不得重叠")
        return self


class SetDeviceBid(StrictModel):
    """修改现有 DEVICE criterion；系数 0 排除设备，0.1–10 为出价倍数。非零倍数仅支持标准手动 CPC。"""

    kind: Literal["set_device_bid"]
    resource: CampaignCriterionRef
    bid_modifier: float = Field(ge=0, le=10, allow_inf_nan=False)

    @field_validator("bid_modifier")
    @classmethod
    def range(cls, value):
        if 0 < value < 0.1:
            raise ValueError("设备系数须为 0 或 0.1–10")
        return value


class RemoveResource(StrictModel):
    """不可恢复地移除 Search 系列、广告组或广告关联。临时停用请用 set_status PAUSED；父资源移除会让下级停止投放，不自动删除系列预算。"""

    kind: Literal["remove_resource"]
    resource: StatusRef


class Sitelink(StrictModel):
    type: Literal["SITELINK"]
    link_text: str = Field(min_length=1, max_length=25)
    final_urls: list[str] = Field(min_length=1, max_length=10)
    description1: str = Field(default="", max_length=35)
    description2: str = Field(default="", max_length=35)

    @model_validator(mode="after")
    def valid(self):
        validate_text(self.link_text, 25)
        validate_urls(self.final_urls)
        if bool(self.description1) != bool(self.description2):
            raise ValueError("附加链接的两行描述需同时提供")
        for value in [self.description1, self.description2]:
            if value:
                validate_text(value, 35)
        return self


class Callout(StrictModel):
    type: Literal["CALLOUT"]
    text: str = Field(min_length=1, max_length=25)

    @field_validator("text")
    @classmethod
    def valid(cls, value):
        return validate_text(value, 25)


class StructuredSnippet(StrictModel):
    type: Literal["STRUCTURED_SNIPPET"]
    header: str = Field(min_length=1, max_length=100)
    values: list[str] = Field(min_length=3, max_length=10)

    @field_validator("values")
    @classmethod
    def valid(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("摘要值不能重复")
        return [validate_text(v, 25) for v in values]


AssetContent = Annotated[Sitelink | Callout | StructuredSnippet, Field(discriminator="type")]


class SetCampaignAssets(StrictModel):
    """替换本系列 field_type 的全部人工素材关联，[] 移除这些关联。相同内容保留；修改内容创建素材并重新关联，旧素材仍在素材库，其他系列/上级/广告组/自动素材不变。支持 SITELINK/CALLOUT/STRUCTURED_SNIPPET。"""

    kind: Literal["set_campaign_assets"]
    campaign: CampaignRef
    field_type: Literal["SITELINK", "CALLOUT", "STRUCTURED_SNIPPET"]
    assets: list[AssetContent] = Field(max_length=20)

    @model_validator(mode="after")
    def same_type(self):
        if any(a.type != self.field_type for a in self.assets):
            raise ValueError("所有素材需与 field_type 一致")
        if len({canonical(a.model_dump()) for a in self.assets}) != len(self.assets):
            raise ValueError("不能包含重复素材")
        return self


SingleChange = Annotated[
    AddKeywords
    | AddNegatives
    | RemoveKeywords
    | SetStatus
    | SetBudget
    | CreateSearch
    | CreateAdGroup
    | UpdateAdGroup
    | CreateAd
    | UpdateAd
    | UpdateKeyword
    | ReplaceKeyword
    | AddAdGroupNegatives
    | UpdateCampaign
    | SetBidding
    | SetTargeting
    | SetSchedule
    | SetDeviceBid
    | RemoveResource
    | SetCampaignAssets,
    Field(discriminator="kind"),
]


Change = SingleChange


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
