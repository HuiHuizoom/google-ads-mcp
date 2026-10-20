"""Strict advertising, asset and conversion contracts (no raw mutations)."""

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
        pattern=r"^customers/[0-9]{10}/(campaigns/[1-9][0-9]*|assetGroups/[1-9][0-9]*|adGroups/[1-9][0-9]*|adGroupAds/[1-9][0-9]*~[1-9][0-9]*)$"
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
    """省略保留。日期为账户时区 yyyy-MM-dd HH:mm:ss；已开始的系列不能改开始时间。search_partners/display_network 仅用于 Search。final_url_suffix='' 清除后缀；final_url_expansion 仅用于 PMax，开关最终网址扩展并保留其他自动化设置。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    start_date_time: str | None = None
    end_date_time: str | None = None
    search_partners: bool | None = None
    display_network: bool | None = None
    positive_geo_target_type: Literal["PRESENCE", "PRESENCE_OR_INTEREST"] | None = None
    negative_geo_target_type: Literal["PRESENCE"] | None = None
    final_url_suffix: str | None = Field(default=None, max_length=2048)
    final_url_expansion: bool | None = None

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
    """移除已支持渠道的系列、广告组/广告关联或 PMax 素材组，不能重新启用。临时停用请用 set_status PAUSED；父资源移除会让下级停止投放。Google 同时移除系列的非共享预算；共享预算和素材库对象保留。"""

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


AssetRef = Annotated[str, StringConstraints(pattern=r"^customers/[0-9]{10}/assets/[1-9][0-9]*$")]
AssetGroupRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/assetGroups/[1-9][0-9]*$")
]
AudienceRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/audiences/[1-9][0-9]*$")
]
PMaxField = Literal[
    "HEADLINE",
    "LONG_HEADLINE",
    "DESCRIPTION",
    "MARKETING_IMAGE",
    "SQUARE_MARKETING_IMAGE",
    "PORTRAIT_MARKETING_IMAGE",
    "YOUTUBE_VIDEO",
    "BUSINESS_NAME",
    "LOGO",
    "LANDSCAPE_LOGO",
]


class TextAssetInput(StrictModel):
    type: Literal["TEXT"]
    text: str = Field(min_length=1, max_length=90)

    @field_validator("text")
    @classmethod
    def valid(cls, value):
        return validate_text(value, 90)


class ImageAssetInput(StrictModel):
    """上传本机明确指定的 PNG/JPEG/GIF；先 inspect_image_asset 获取 sha256。预览及提交均核对同一文件内容，不裁剪。"""

    type: Literal["IMAGE"]
    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=80)


class VideoAssetInput(StrictModel):
    """注册已有 YouTube 视频素材；不上传本地视频到 YouTube。"""

    type: Literal["YOUTUBE_VIDEO"]
    youtube_video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    name: str = Field(min_length=1, max_length=80)


UploadAsset = Annotated[
    TextAssetInput | ImageAssetInput | VideoAssetInput, Field(discriminator="type")
]


class CreateAssets(StrictModel):
    """创建或由 Google 去重返回素材库对象；不关联投放。不原地修改共享素材，替换内容应创建后重新关联。"""

    kind: Literal["create_assets"]
    assets: list[UploadAsset] = Field(min_length=1, max_length=20)


class AssetLink(StrictModel):
    asset: AssetRef
    field_type: PMaxField


class AssetGroupContent(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    final_urls: list[str] = Field(min_length=1, max_length=10)
    assets: list[AssetLink] = Field(min_length=1, max_length=100)

    @field_validator("final_urls")
    @classmethod
    def urls(cls, values):
        return validate_urls(values)


class CreatePMax(StrictModel):
    """创建标准非零售 PMax：独立日预算、PAUSED 系列及素材组、启用品牌指南。素材需先上传或查找；不启用投放，不设置 Merchant Center 商品源。"""

    kind: Literal["create_pmax"]
    name: str = Field(min_length=1, max_length=80)
    currency: Currency
    daily_amount: Money
    bidding: Literal["MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE"]
    target_cpa: Money | None = None
    target_roas: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    geo_target_ids: list[PositiveId] = Field(min_length=1, max_length=20)
    language_ids: list[PositiveId] = Field(min_length=1, max_length=10)
    eu_political_advertising: Literal["DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"]
    brand_assets: list[AssetLink] = Field(min_length=2, max_length=6)
    asset_groups: list[AssetGroupContent] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def valid(self):
        micros(self.daily_amount)
        if self.target_cpa is not None:
            micros(self.target_cpa)
            if self.bidding != "MAXIMIZE_CONVERSIONS":
                raise ValueError("target_cpa 只适用于 MAXIMIZE_CONVERSIONS")
        if self.target_roas is not None and self.bidding != "MAXIMIZE_CONVERSION_VALUE":
            raise ValueError("target_roas 只适用于 MAXIMIZE_CONVERSION_VALUE")
        for values in [self.geo_target_ids, self.language_ids, [g.name for g in self.asset_groups]]:
            if len(set(values)) != len(values):
                raise ValueError("地区、语言、素材组名称不可重复")
        return self


class CreateAssetGroup(StrictModel):
    """向已有标准 PMax 添加 PAUSED 素材组，同时原子关联满足最低要求的素材。"""

    kind: Literal["create_asset_group"]
    campaign: CampaignRef
    group: AssetGroupContent


class AssetGroupPatch(Patch):
    """省略保留；创建后修改名称、最终网址、显示路径。路径可用空字符串清空。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    final_urls: list[str] | None = Field(default=None, min_length=1, max_length=10)
    path1: str | None = Field(default=None, max_length=15)
    path2: str | None = Field(default=None, max_length=15)

    @model_validator(mode="after")
    def valid(self):
        if self.final_urls is not None:
            validate_urls(self.final_urls)
        for value in [self.path1, self.path2]:
            if value:
                validate_text(value, 15)
                if "/" in value or any(c.isspace() for c in value):
                    raise ValueError("显示路径不能含斜杠或空白")
        return self


class UpdateAssetGroup(StrictModel):
    kind: Literal["update_asset_group"]
    asset_group: AssetGroupRef
    patch: AssetGroupPatch


class SetAssetGroupAssets(StrictModel):
    """各传入 field_type 的完整目标素材集合；未传入类型保留。[] 删除该类型人工关联，但不可低于 Google 最低素材要求。自动来源关联保留，共享素材内容不变。"""

    kind: Literal["set_asset_group_assets"]
    asset_group: AssetGroupRef
    assets_by_field: dict[PMaxField, list[AssetRef]] = Field(min_length=1, max_length=11)


class SetAssetGroupSignals(StrictModel):
    """受众和搜索主题是优化提示，不是硬性定向。仅替换提供的维度，省略保留，[] 清空；信号不可原地更新，因此差异通过添加/移除实现。audiences 使用已有 Audience。"""

    kind: Literal["set_asset_group_signals"]
    asset_group: AssetGroupRef
    audiences: list[AudienceRef] | None = Field(default=None, max_length=10)
    search_themes: list[str] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def valid(self):
        if not ({"audiences", "search_themes"} & self.model_fields_set):
            raise ValueError("至少提供一种信号；[] 清空，省略保留")
        for key in self.model_fields_set - {"kind", "asset_group"}:
            values = getattr(self, key)
            if values is None or len({v.casefold() for v in values}) != len(values):
                raise ValueError("不接受 null 或重复信号")
        for theme in self.search_themes or []:
            validate_text(theme, 80)
        return self


class SetPMaxBrandAssets(StrictModel):
    """替换启用品牌指南 PMax 的完整人工品牌关联（BUSINESS_NAME/LOGO/LANDSCAPE_LOGO），保留其他类型及自动素材；必须保持一个商家名和合规 Logo。"""

    kind: Literal["set_pmax_brand_assets"]
    campaign: CampaignRef
    assets: list[AssetLink] = Field(min_length=2, max_length=6)


ConversionRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/conversionActions/[1-9][0-9]*$")
]
CustomGoalRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/customConversionGoals/[1-9][0-9]*$")
]
UserListRef = Annotated[
    str, StringConstraints(pattern=r"^customers/[0-9]{10}/userLists/[1-9][0-9]*$")
]
ConversionCategory = Literal[
    "DEFAULT",
    "PAGE_VIEW",
    "PURCHASE",
    "SIGNUP",
    "DOWNLOAD",
    "ADD_TO_CART",
    "BEGIN_CHECKOUT",
    "SUBSCRIBE_PAID",
    "PHONE_CALL_LEAD",
    "IMPORTED_LEAD",
    "SUBMIT_LEAD_FORM",
    "BOOK_APPOINTMENT",
    "REQUEST_QUOTE",
    "GET_DIRECTIONS",
    "OUTBOUND_CLICK",
    "CONTACT",
    "ENGAGEMENT",
    "QUALIFIED_LEAD",
    "CONVERTED_LEAD",
]
ConversionOrigin = Literal[
    "WEBSITE", "APP", "CALL_FROM_ADS", "STORE", "YOUTUBE_HOSTED", "GOOGLE_HOSTED"
]


class ConversionValues(Patch):
    """省略保留原值；0 是有效转化价值。币种采用 ISO 4217。"""

    default_value: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    default_currency_code: Currency | None = None
    always_use_default_value: bool | None = None


class ConversionActionPatch(Patch):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    # Google rejects HIDDEN for the three conversion types this writer supports.
    # Disable recording through remove_conversion_action, not a hidden status.
    status: Literal["ENABLED"] | None = None
    category: ConversionCategory | None = None
    primary_for_goal: bool | None = None
    counting_type: Literal["ONE_PER_CLICK", "MANY_PER_CLICK"] | None = None
    click_through_lookback_window_days: int | None = Field(default=None, ge=1, le=90)
    view_through_lookback_window_days: int | None = Field(default=None, ge=1, le=30)
    value_settings: ConversionValues | None = None


class CreateConversionAction(StrictModel):
    """建立网站/离线转化配置，不安装埋点、不上传事件；默认 secondary，不能把创建成功当成收到转化。"""

    kind: Literal["create_conversion_action"]
    name: str = Field(min_length=1, max_length=80)
    type: Literal["WEBPAGE", "UPLOAD_CLICKS", "UPLOAD_CALLS"]
    category: ConversionCategory
    primary_for_goal: bool = False
    counting_type: Literal["ONE_PER_CLICK", "MANY_PER_CLICK"] = "ONE_PER_CLICK"
    click_through_lookback_window_days: int = Field(default=30, ge=1, le=90)
    value_settings: ConversionValues | None = None
    expected_campaigns: list[CampaignRef] = Field(max_length=1000)


class UpdateConversionAction(StrictModel):
    """创建后修改配置；须列出账户全部未移除系列以核对影响范围。不修改外部导入类型、所有权或类型。"""

    kind: Literal["update_conversion_action"]
    conversion_action: ConversionRef
    patch: ConversionActionPatch
    expected_campaigns: list[CampaignRef] = Field(max_length=1000)


class RemoveConversionAction(StrictModel):
    kind: Literal["remove_conversion_action"]
    conversion_action: ConversionRef
    expected_campaigns: list[CampaignRef] = Field(max_length=1000)


class GoalToggle(StrictModel):
    category: ConversionCategory
    origin: ConversionOrigin
    biddable: bool


class SetConversionGoals(StrictModel):
    """修改已由 Google 生成的目标。campaign 省略表示账户默认目标；提供时只修改该系列。CUSTOMER 恢复继承；CAMPAIGN 切换为系列级目标。只改传入目标，未传入保留。"""

    kind: Literal["set_conversion_goals"]
    campaign: CampaignRef | None = None
    goals: list[GoalToggle] = Field(default_factory=list, max_length=100)
    goal_config_level: Literal["CUSTOMER", "CAMPAIGN"] | None = None
    custom_conversion_goal: CustomGoalRef | Literal[""] | None = None
    expected_campaigns: list[CampaignRef] = Field(max_length=1000)

    @model_validator(mode="after")
    def valid(self):
        if not self.campaign and (
            self.goal_config_level is not None or self.custom_conversion_goal is not None
        ):
            raise ValueError("目标来源和自定义目标只适用于 campaign")
        if (
            not self.goals
            and self.goal_config_level is None
            and self.custom_conversion_goal is None
        ):
            raise ValueError("至少修改一个目标或目标来源")
        if self.goal_config_level == "CUSTOMER" and (self.goals or self.custom_conversion_goal):
            raise ValueError("恢复账户默认目标时不能同时指定系列目标")
        keys = [(g.category, g.origin) for g in self.goals]
        if len(keys) != len(set(keys)):
            raise ValueError("重复转化目标")
        return self


class CreateCustomConversionGoal(StrictModel):
    kind: Literal["create_custom_conversion_goal"]
    name: str = Field(min_length=1, max_length=80)
    conversion_actions: list[ConversionRef] = Field(min_length=1, max_length=100)


class CustomGoalPatch(Patch):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    conversion_actions: list[ConversionRef] | None = Field(
        default=None, min_length=1, max_length=100
    )


class UpdateCustomConversionGoal(StrictModel):
    kind: Literal["update_custom_conversion_goal"]
    custom_conversion_goal: CustomGoalRef
    patch: CustomGoalPatch
    expected_campaigns: list[CampaignRef] = Field(max_length=1000)


class RemoveCustomConversionGoal(StrictModel):
    kind: Literal["remove_custom_conversion_goal"]
    custom_conversion_goal: CustomGoalRef
    expected_campaigns: list[CampaignRef] = Field(max_length=1000)


class ChannelAdContent(StrictModel):
    """完整的新文案及素材组合；图片/视频使用 create_assets 或素材库已有资源。编辑通过原子替换为暂停广告完成，旧广告 ID/统计不继承。"""

    format: Literal["RESPONSIVE_DISPLAY", "DEMAND_GEN_IMAGE", "DEMAND_GEN_VIDEO", "APP"]
    headlines: list[str] = Field(min_length=1, max_length=5)
    descriptions: list[str] = Field(min_length=1, max_length=5)
    business_name: str | None = Field(default=None, min_length=1, max_length=25)
    long_headline: str | None = Field(default=None, min_length=1, max_length=90)
    final_urls: list[str] = Field(default_factory=list, max_length=10)
    marketing_images: list[AssetRef] = Field(default_factory=list, max_length=20)
    square_marketing_images: list[AssetRef] = Field(default_factory=list, max_length=20)
    portrait_marketing_images: list[AssetRef] = Field(default_factory=list, max_length=20)
    logo_images: list[AssetRef] = Field(default_factory=list, max_length=5)
    videos: list[AssetRef] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def valid(self):
        for key, width in [
            ("headlines", 40 if self.format == "DEMAND_GEN_IMAGE" else 30),
            ("descriptions", 90),
        ]:
            values = getattr(self, key)
            if len(set(values)) != len(values):
                raise ValueError("文案不得重复")
            for value in values:
                validate_text(value, width)
        for value, width in [(self.business_name, 25), (self.long_headline, 90)]:
            if value is not None:
                validate_text(value, width)
        validate_urls(self.final_urls)
        for key in [
            "marketing_images",
            "square_marketing_images",
            "portrait_marketing_images",
            "logo_images",
            "videos",
        ]:
            if len(set(getattr(self, key))) != len(getattr(self, key)):
                raise ValueError("同一素材类型不可重复")
        if self.format != "APP" and (not self.business_name or not self.final_urls):
            raise ValueError("此格式需要 business_name 和 final_urls")
        if self.format in {"RESPONSIVE_DISPLAY", "DEMAND_GEN_IMAGE"} and (
            not self.marketing_images or not self.square_marketing_images
        ):
            raise ValueError("需要横版和正方形营销图片")
        if self.format in {"RESPONSIVE_DISPLAY", "DEMAND_GEN_VIDEO"} and not self.long_headline:
            raise ValueError("此格式需要 long_headline")
        if self.format.startswith("DEMAND_GEN") and not self.logo_images:
            raise ValueError("Demand Gen 需要 Logo")
        if self.format == "DEMAND_GEN_VIDEO" and not self.videos:
            raise ValueError("视频广告需要已有 YouTube 视频素材")
        if self.format == "DEMAND_GEN_IMAGE" and (self.videos or self.long_headline):
            raise ValueError("Demand Gen 图片格式不接收 videos/long_headline")
        if self.format == "DEMAND_GEN_VIDEO" and any(
            [self.marketing_images, self.square_marketing_images, self.portrait_marketing_images]
        ):
            raise ValueError("视频格式图片仅使用 logo_images")
        if self.format == "RESPONSIVE_DISPLAY" and (
            self.portrait_marketing_images
            or len(self.marketing_images) + len(self.square_marketing_images) > 15
        ):
            raise ValueError("Display 不接受竖版图片；营销图片合计最多 15")
        if self.format == "APP":
            if any([self.final_urls, self.business_name, self.long_headline, self.logo_images]):
                raise ValueError("App 安装广告从应用商店获取网址/商家资料，不接受这些字段")
            if len(self.descriptions) < 2:
                raise ValueError("App 广告至少两条描述")
        return self


class ChannelBidding(StrictModel):
    strategy: Literal[
        "MANUAL_CPC",
        "MAXIMIZE_CONVERSIONS",
        "MAXIMIZE_CONVERSION_VALUE",
        "TARGET_CPA",
        "TARGET_ROAS",
    ]
    target_cpa: Money | None = None
    target_roas: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid(self):
        if self.target_cpa is not None:
            micros(self.target_cpa)
            if self.strategy not in {"MAXIMIZE_CONVERSIONS", "TARGET_CPA"}:
                raise ValueError("此策略不接受 target_cpa")
        if self.target_roas is not None and self.strategy not in {
            "MAXIMIZE_CONVERSION_VALUE",
            "TARGET_ROAS",
        }:
            raise ValueError("此策略不接受 target_roas")
        if (self.strategy == "TARGET_CPA" and self.target_cpa is None) or (
            self.strategy == "TARGET_ROAS" and self.target_roas is None
        ):
            raise ValueError("目标出价策略必须提供目标")
        return self


class AppSettings(StrictModel):
    """安装推广 APP_CAMPAIGN；商店 ID 创建后不可修改。不包含互动或预注册子类型。"""

    app_id: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.]+$")
    app_store: Literal["GOOGLE_APP_STORE", "APPLE_APP_STORE"]
    goal: Literal[
        "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
        "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_INSTALL_COST",
        "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST",
        "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND",
    ]
    conversion_actions: list[ConversionRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid(self):
        if self.app_store == "APPLE_APP_STORE" and not self.app_id.isdigit():
            raise ValueError("Apple App ID 必须为数字")
        if self.goal != "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST" and not self.conversion_actions:
            raise ValueError("应用内操作/价值优化需要 conversion_actions")
        return self


class ChannelGroup(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    cpc_bid: Money | None = None
    optimized_targeting: bool = False
    audience: AudienceRef | None = None
    user_lists: list[UserListRef] = Field(default_factory=list, max_length=20)
    geo_target_ids: list[PositiveId] | None = Field(default=None, min_length=1, max_length=20)
    language_ids: list[PositiveId] | None = Field(default=None, min_length=1, max_length=10)
    ads: list[ChannelAdContent] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def valid(self):
        for values in [self.geo_target_ids, self.language_ids]:
            if values is not None and len(values) != len(set(values)):
                raise ValueError("地区、语言不可重复")
        return self


class CreateChannelCampaign(StrictModel):
    """原子创建 Display、Demand Gen 或 App 安装系列及广告组/广告，全部 PAUSED。App 商店身份不可后改；其他维护使用更新和定向动作。"""

    kind: Literal["create_channel_campaign"]
    channel: Literal["DISPLAY", "DEMAND_GEN", "APP"]
    name: str = Field(min_length=1, max_length=80)
    currency: Currency
    daily_amount: Money
    bidding: ChannelBidding
    geo_target_ids: list[PositiveId] = Field(min_length=1, max_length=20)
    language_ids: list[PositiveId] = Field(min_length=1, max_length=10)
    eu_political_advertising: Literal["DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"]
    app: AppSettings | None = None
    ad_groups: list[ChannelGroup] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def valid(self):
        micros(self.daily_amount)
        if (self.channel == "APP") != (self.app is not None):
            raise ValueError("只有 APP 且必须提供 app 设置")
        for values in [self.geo_target_ids, self.language_ids, [g.name for g in self.ad_groups]]:
            if len(values) != len(set(values)):
                raise ValueError("地区、语言、广告组名称不可重复")
        return self


class CreateChannelAdGroup(StrictModel):
    kind: Literal["create_channel_ad_group"]
    campaign: CampaignRef
    group: ChannelGroup


class CreateChannelAd(StrictModel):
    kind: Literal["create_channel_ad"]
    ad_group: GroupRef
    ad: ChannelAdContent


class ReplaceChannelAd(StrictModel):
    """原子移除该广告关联并创建新 PAUSED 广告；其他组的共享广告关联保留，ID 和历史统计不继承。"""

    kind: Literal["replace_channel_ad"]
    ad_group_ad: AdRef
    ad: ChannelAdContent


class ChannelGroupPatch(Patch):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    cpc_bid: Money | None = None
    optimized_targeting: bool | None = None


class UpdateChannelAdGroup(StrictModel):
    kind: Literal["update_channel_ad_group"]
    ad_group: GroupRef
    patch: ChannelGroupPatch


class SetChannelBidding(StrictModel):
    kind: Literal["set_channel_bidding"]
    campaign: CampaignRef
    bidding: ChannelBidding
    app_goal: AppSettings | None = None


class SetChannelGroupTargeting(StrictModel):
    """Display 使用 user_lists；Demand Gen 使用 audience，空字符串移除。Demand Gen 可按广告组设置地区/语言；省略维度保留、[] 清空。"""

    kind: Literal["set_channel_group_targeting"]
    ad_group: GroupRef
    audience: AudienceRef | Literal[""] | None = None
    user_lists: list[UserListRef] | None = Field(default=None, max_length=20)
    geo_target_ids: list[PositiveId] | None = Field(default=None, max_length=20)
    language_ids: list[PositiveId] | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def valid(self):
        provided = self.model_fields_set - {"kind", "ad_group"}
        if not provided or any(getattr(self, k) is None for k in provided):
            raise ValueError("至少设置一个维度；省略保留，不接受 null")
        for k in provided - {"audience"}:
            values = getattr(self, k)
            if len(values) != len(set(values)):
                raise ValueError("重复定向项")
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
    | SetCampaignAssets
    | CreateAssets
    | CreatePMax
    | CreateAssetGroup
    | UpdateAssetGroup
    | SetAssetGroupAssets
    | SetAssetGroupSignals
    | SetPMaxBrandAssets
    | CreateConversionAction
    | UpdateConversionAction
    | RemoveConversionAction
    | SetConversionGoals
    | CreateCustomConversionGoal
    | UpdateCustomConversionGoal
    | RemoveCustomConversionGoal
    | CreateChannelCampaign
    | CreateChannelAdGroup
    | CreateChannelAd
    | ReplaceChannelAd
    | UpdateChannelAdGroup
    | SetChannelBidding
    | SetChannelGroupTargeting,
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
            *[getattr(g, "keywords", []) for g in groups],
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
