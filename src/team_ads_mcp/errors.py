"""Return stable error codes and recovery actions; never raw provider exceptions."""

from __future__ import annotations


class AdsError(Exception):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False, request_id=None, details=None
    ):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.request_id, self.details = retryable, request_id, details

    def public(self):
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "details": self.details,
        }


def safe_error(exc: Exception) -> AdsError:
    if isinstance(exc, AdsError):
        return exc
    import grpc
    from google.ads.googleads.errors import GoogleAdsException
    from google.api_core import exceptions as api
    from google.auth.exceptions import DefaultCredentialsError, RefreshError

    if isinstance(exc, (RefreshError, DefaultCredentialsError)):
        return AdsError(
            "AUTH_RECONNECT_REQUIRED", "Google 授权缺失、失效或被撤销，请在本机重新登录"
        )
    if isinstance(exc, (api.Unauthenticated, api.Unauthorized)):
        return AdsError(
            "AUTH_RECONNECT_REQUIRED", "Google 未接受当前身份，请检查 OAuth/ADC 并重新授权"
        )
    if isinstance(exc, (api.PermissionDenied, api.Forbidden)):
        return AdsError(
            "GOOGLE_ACCESS_DENIED",
            "检查 Google Ads API 是否启用、Cloud 访问等级及 Ads 账户/MCC 角色",
        )
    if isinstance(exc, GoogleAdsException):
        codes = []
        for error in exc.failure.errors:
            pb = error.error_code._pb
            field = pb.WhichOneof("error_code")
            if field:
                desc = pb.DESCRIPTOR.fields_by_name[field]
                codes.append(desc.enum_type.values_by_number[getattr(pb, field)].name)
                if field == "internal_error":
                    return AdsError(
                        "TRANSPORT_UNKNOWN",
                        "Google 内部错误使结果不明；禁止自动重发，先核对",
                        request_id=exc.request_id,
                    )
        joined = " ".join(codes)
        if "CLOUD_PROJECT" in joined or "DEVELOPER_TOKEN" in joined:
            message = "检查 OAuth 凭据所属 Cloud 项目的 Google Ads API 访问等级"
        elif (
            "USER_PERMISSION" in joined
            or "AUTHORIZATION" in joined
            or "ACTION_NOT_PERMITTED" in joined
        ):
            message = "检查 Ads 账户角色、MCC 路径与 Cloud 项目访问等级；此错误不能单独确定是哪一项"
        elif "AUTHENTICATION" in joined or "OAUTH" in joined:
            message = "检查授权身份和 adwords scope；必要时重新登录"
        else:
            message = "Google 拒绝此请求；根据错误码修正输入或账户配置后重新预览"
        return AdsError(
            "GOOGLE_REJECTED", message, request_id=exc.request_id, details={"codes": codes}
        )
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            api.DeadlineExceeded,
            api.ServiceUnavailable,
            api.InternalServerError,
            grpc.RpcError,
        ),
    ):
        return AdsError("TRANSPORT_UNKNOWN", "请求结果不明；先核对账本和远端状态，禁止自动重试创建")
    # Do not serialize repr/str: provider errors may embed headers, tokens or response bodies.
    return AdsError("INTERNAL_ERROR", "内部错误；未输出原始异常以保护凭据，请查看测试或联系维护者")
