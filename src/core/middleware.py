# src/core/middleware.py

import logging
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.core.config import IS_PRODUCTION

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # セキュリティヘッダーの追加
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # HSTS (本番環境のみ)
        if request.url.scheme == "https" or IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests for security monitoring."""

    # 【追加】センシティブなパスのログ出力を制限
    SENSITIVE_PATHS = {"/auth/discord/callback", "/api/billing/webhook"}

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()

        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path

        response = await call_next(request)

        process_time = time.time() - start_time

        # 異常なリクエストを検出
        if response.status_code >= 400:
            # 【変更】センシティブなパスの場合はクエリパラメータを隠す
            if path in self.SENSITIVE_PATHS:
                log_path = path
            else:
                log_path = str(request.url.path)

            logger.warning(
                f"Request failed: {request.method} {log_path} "
                f"status={response.status_code} ip={client_ip} "
                f"time={process_time:.3f}s"
            )

        return response
