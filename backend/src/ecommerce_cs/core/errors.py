"""统一错误类型与响应格式。"""


class AppError(Exception):
    def __init__(self, message: str, *, code: str = "app_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, code="not_found", status_code=404)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "请先登录"):
        super().__init__(message, code="unauthorized", status_code=401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "无权访问"):
        super().__init__(message, code="forbidden", status_code=403)


class ConflictError(AppError):
    def __init__(self, message: str = "资源冲突"):
        super().__init__(message, code="conflict", status_code=409)


class RateLimitError(AppError):
    def __init__(self, message: str = "操作过于频繁，请稍后再试", *, retry_after: int = 60):
        super().__init__(message, code="rate_limited", status_code=429)
        self.retry_after = max(1, retry_after)
