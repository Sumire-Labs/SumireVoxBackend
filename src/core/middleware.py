# src/core/middleware.py

import logging
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

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
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests for security monitoring."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()

        # リクエスト情報をログ
        client_ip = request.client.host if request.client else "unknown"

        response = await call_next(request)

        process_time = time.time() - start_time

        # 異常なリクエストを検出
        if response.status_code >= 400:
            logger.warning(
                f"Request failed: {request.method} {request.url.path} "
                f"status={response.status_code} ip={client_ip} "
                f"time={process_time:.3f}s"
            )

        return response
