import asyncio
from typing import Any

import httpx
from openai import AsyncOpenAI, APIError, APIStatusError, APITimeoutError

from app.config import settings


class BridgeConfigurationError(Exception):
    pass


class BridgeResponseError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class BridgeClient:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            base_url="https://wekoya-api.wekoya.tech/v1",
            api_key=settings.wekoya_api_key,
            timeout=30.0,
        )
        self.tools_supported = False
        self.catalogue_checked = False

    async def ensure_end_user(self, customer_id: str) -> tuple[bool, str]:
        if not settings.wekoya_api_key:
            return False, "WEKOYA_API_KEY is not configured."
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                response = await http.post(
                    "https://wekoya-api.wekoya.tech/v1/end-users",
                    headers={"Authorization": f"Bearer {settings.wekoya_api_key}"},
                    json={
                        "external_id": customer_id,
                        "allowance": 5000,
                        "period": "monthly",
                    },
                )
            if response.status_code in (200, 201, 409):
                return True, ""
            if response.status_code in (400, 404):
                return False, "The demo customer could not be provisioned with Wekoya."
            return False, "Wekoya could not provision the selected demo customer."
        except httpx.TimeoutException:
            return False, "Wekoya timed out while provisioning the demo customer."
        except httpx.HTTPError:
            return False, "Wekoya could not provision the selected demo customer."

    async def check_catalogue(self) -> tuple[bool, str]:
        if not settings.wekoya_api_key:
            return False, "WEKOYA_API_KEY is not configured."
        try:
            models = await self.client.models.list()
            entries = getattr(models, "data", []) or []
            model = next((item for item in entries if getattr(item, "id", None) == settings.wekoya_model), None)
            if model is None:
                return False, f"The configured model '{settings.wekoya_model}' was not found in the Wekoya catalogue."
            metadata = getattr(model, "metadata", None) or getattr(model, "capabilities", None) or {}
            if isinstance(metadata, dict):
                self.tools_supported = bool(metadata.get("tools") or metadata.get("tool_calling") or metadata.get("function_calling"))
            else:
                self.tools_supported = bool(getattr(metadata, "tools", False) or getattr(metadata, "tool_calling", False))
            self.catalogue_checked = True
            if not self.tools_supported:
                return False, f"The configured model '{settings.wekoya_model}' does not advertise function/tool support."
            return True, ""
        except Exception as exc:
            return False, f"Could not verify the configured model in the Wekoya catalogue: {safe_error_text(exc)}"

    async def create(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        customer_id: str,
    ):
        if not settings.wekoya_api_key:
            raise BridgeResponseError("The support assistant is not configured. Please try again later or escalate to a human agent.", 503)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request: dict[str, Any] = {
                    "model": settings.wekoya_model,
                    "messages": messages,
                    "extra_headers": {"X-Bridge-End-User": customer_id},
                }
                if tools:
                    request["tools"] = tools
                return await self.client.chat.completions.create(**request)
            except APITimeoutError as exc:
                last_error = exc
                raise BridgeResponseError("The AI service timed out. Please try again or escalate to a human agent.", 504)
            except APIStatusError as exc:
                status = getattr(exc, "status_code", 500)
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(0.25 * (2 ** attempt))
                    continue
                return map_status_error(status)
            except APIError as exc:
                last_error = exc
                raise BridgeResponseError("The AI service is temporarily unavailable. Please try again or escalate to a human agent.", 502)

        raise BridgeResponseError("The AI service is temporarily unavailable after retries.", 502)


def map_status_error(status: int) -> BridgeResponseError:
    if status in (401, 403):
        return BridgeResponseError("The support assistant could not authenticate with the AI service. Please escalate to a human agent.", 502)
    if status == 402:
        return BridgeResponseError("The Wekoya wallet cannot currently fund this request. Please escalate to a human agent.", 402)
    if status == 429:
        return BridgeResponseError("The support assistant is busy due to rate limits. Please try again shortly or escalate.", 429)
    if status >= 500:
        return BridgeResponseError("The AI service is temporarily unavailable. Please try again or escalate to a human agent.", 502)
    return BridgeResponseError("The AI service could not complete the request. Please escalate to a human agent.", 502)


def safe_error_text(exc: Exception) -> str:
    return exc.__class__.__name__
