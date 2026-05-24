from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


class NotFoundError(AppError):
    def __init__(self, detail: str = "Not found") -> None:
        super().__init__(404, detail)


class ConflictError(AppError):
    def __init__(self, detail: str = "Conflict") -> None:
        super().__init__(409, detail)


class UnauthorizedError(AppError):
    def __init__(self, detail: str = "Unauthorized") -> None:
        super().__init__(401, detail)


class ForbiddenError(AppError):
    def __init__(self, detail: str = "Forbidden") -> None:
        super().__init__(403, detail)


class ValidationError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
