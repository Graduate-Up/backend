"""졸업요건 챗봇과 과목 추천이 쓰는 생성형 AI 호출.

기본은 Google Gemini API(무료 등급 사용 가능)이며, OpenAI Responses API도 쓸 수 있다.
- JOLUP_AI_PROVIDER: gemini | openai. 비우면 GEMINI_API_KEY가 있으면 gemini, 없고 OPENAI_API_KEY가 있으면 openai.
- Gemini: GEMINI_API_KEY, 모델 JOLUP_GEMINI_MODEL (기본 gemini-flash-latest: 최신 Flash 모델 별칭)
  일시적인 HTTP 오류, 연결 실패 또는 출력 한도 초과 시 예비 모델(JOLUP_GEMINI_FALLBACK_MODEL)로 한 번 더 시도.
  비워 두면 예비 모델을 쓰지 않는다.
- OpenAI: OPENAI_API_KEY, 모델은 호출하는 쪽이 정한 환경변수 (기본 gpt-5.4-mini)

응답은 정해 둔 JSON 스키마로만 받는다. 실패하면 원인을 담은 AIUnavailable을 던진다(키·본문은 담지 않는다).
"""

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"
GEMINI_FALLBACK_MODEL = "gemini-flash-lite-latest"
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
OPENAI_URL = "https://api.openai.com/v1/responses"
OPENAI_DEFAULT_MODEL = "gpt-5.4-mini"


class AIUnavailable(Exception):
    """AI 답변을 얻지 못함. 메시지는 로그용 원인 (예: "gemini HTTP 429 RESOURCE_EXHAUSTED")."""


class AIOutputLimit(AIUnavailable):
    """The model ran out of output tokens before completing an answer."""


def ai_provider() -> str | None:
    chosen = os.getenv("JOLUP_AI_PROVIDER", "").strip().lower()
    if chosen in ("gemini", "openai"):
        return chosen
    if os.getenv("GEMINI_API_KEY", "").strip():
        return "gemini"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai"
    return None


def generate_json(
    *, name: str, instructions: str, payload: dict, schema: dict, max_output_tokens: int, timeout: float,
    openai_model_envs: tuple[str, ...] = ("JOLUP_OPENAI_MODEL",),
) -> tuple[dict, str]:
    """instructions(시스템 지시)와 payload(JSON 입력)를 보내 schema에 맞는 JSON 객체를 받는다.

    반환: (응답 객체, 사용한 provider)
    """
    provider = ai_provider()
    if provider is None:
        raise AIUnavailable("GEMINI_API_KEY (또는 OPENAI_API_KEY) is missing")
    text = json.dumps(payload, ensure_ascii=False)
    timeouts = httpx.Timeout(timeout, connect=5.0)
    try:
        if provider == "gemini":
            raw = _gemini(name, instructions, text, schema, max_output_tokens, timeouts)
        else:
            raw = _openai(name, instructions, text, schema, max_output_tokens, timeouts, openai_model_envs)
        result = json.loads(raw)
    except httpx.HTTPStatusError as exc:
        raise AIUnavailable(f"{provider} HTTP {exc.response.status_code} {_error_code(exc.response)}") from exc
    except httpx.TimeoutException as exc:
        raise AIUnavailable(f"{provider} request timed out") from exc
    except httpx.HTTPError as exc:
        raise AIUnavailable(f"{provider} connection or transport error") from exc
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise AIUnavailable(f"{provider} unexpected response format") from exc
    if not isinstance(result, dict):
        raise AIUnavailable(f"{provider} unexpected response format")
    return result, provider


def _key(env: str) -> str:
    key = os.getenv(env, "").strip()
    if not key:
        raise AIUnavailable(f"{env} is missing")
    return key


def gemini_models() -> list[str]:
    primary = os.getenv("JOLUP_GEMINI_MODEL", "").strip() or GEMINI_DEFAULT_MODEL
    fallback = os.getenv("JOLUP_GEMINI_FALLBACK_MODEL", GEMINI_FALLBACK_MODEL).strip()
    return list(dict.fromkeys(model for model in (primary, fallback) if model))


def _gemini(name, instructions, text, schema, max_output_tokens, timeout) -> str:
    """일시적인 실패는 설정된 예비 모델로 한 번 더 시도한다."""
    key = _key("GEMINI_API_KEY")
    models = gemini_models()
    if len(models) > 1:
        timeout = httpx.Timeout(timeout.read / len(models), connect=timeout.connect)
    for index, model in enumerate(models):
        try:
            return _gemini_once(model, key, instructions, text, schema, max_output_tokens, timeout)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in RETRYABLE_STATUS or index + 1 == len(models):
                raise
            logger.warning("gemini HTTP %s %s on %s; retrying with %s",
                           status, _error_code(exc.response), model, models[index + 1])
        except (httpx.TimeoutException, httpx.NetworkError, AIOutputLimit) as exc:
            if index + 1 == len(models):
                raise
            logger.warning("gemini %s on %s; retrying with %s",
                           type(exc).__name__, model, models[index + 1])
    raise AIUnavailable("gemini no model configured")


def _gemini_once(model, key, instructions, text, schema, max_output_tokens, timeout) -> str:
    response = httpx.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": key},
        json={
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema(schema),
                "maxOutputTokens": max_output_tokens,
            },
        },
        timeout=timeout,
        follow_redirects=False,
    )
    response.raise_for_status()
    data = response.json()
    if (data.get("promptFeedback") or {}).get("blockReason"):
        raise AIUnavailable(f"gemini prompt blocked ({data['promptFeedback']['blockReason']})")
    candidate = data["candidates"][0]
    parts = [part["text"] for part in (candidate.get("content") or {}).get("parts", [])
             if "text" in part and not part.get("thought")]
    finish = candidate.get("finishReason")
    if finish == "MAX_TOKENS":
        raise AIOutputLimit("gemini output token limit reached")
    if not parts or finish not in (None, "STOP"):
        raise AIUnavailable(f"gemini generation did not complete ({finish})")
    return "".join(parts)


def gemini_schema(schema: dict) -> dict:
    """JSON Schema(OpenAI strict 형식)를 Gemini responseSchema(OpenAPI 부분집합) 형식으로 바꾼다."""
    result = {}
    for key, value in schema.items():
        if key == "type":
            result["type"] = value.upper()
        elif key == "properties":
            result["properties"] = {name: gemini_schema(child) for name, child in value.items()}
            result["propertyOrdering"] = list(value)
        elif key == "items":
            result["items"] = gemini_schema(value)
        elif key in ("required", "enum", "description"):
            result[key] = value
    return result


def _openai(name, instructions, text, schema, max_output_tokens, timeout, model_envs) -> str:
    key = _key("OPENAI_API_KEY")
    model = next((os.getenv(env, "").strip() for env in model_envs if os.getenv(env, "").strip()),
                 OPENAI_DEFAULT_MODEL)
    response = httpx.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model, "store": False, "max_output_tokens": max_output_tokens,
            "instructions": instructions, "input": text,
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        },
        timeout=timeout,
        follow_redirects=False,
    )
    response.raise_for_status()
    data = response.json()
    if data["status"] == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason")
        raise AIUnavailable("openai output token limit reached" if reason == "max_output_tokens"
                            else "openai response incomplete")
    if data["status"] != "completed":
        raise AIUnavailable("openai generation did not complete")
    for item in data["output"]:
        if item["type"] == "message":
            for content in item["content"]:
                if content["type"] == "output_text":
                    return content["text"]
    raise AIUnavailable("openai no answer in completed response")


def _error_code(response: httpx.Response) -> str:
    """오류 응답의 코드만 뽑는다 (메시지 본문은 로그에 남기지 않는다)."""
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return "unspecified"
    if not isinstance(error, dict):
        return "unspecified"
    reasons = [detail.get("reason") for detail in error.get("details") or [] if isinstance(detail, dict)]
    parts = [error.get("status"), error.get("code") if isinstance(error.get("code"), str) else None,
             error.get("type"), *reasons]
    known = [str(part) for part in parts if part and str(part).replace("_", "").isalnum() and len(str(part)) <= 64]
    return " ".join(dict.fromkeys(known)) or "unspecified"
