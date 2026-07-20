import json
from typing import Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings

OutputModel = TypeVar("OutputModel", bound=BaseModel)


class AgentProviderError(RuntimeError):
    pass


class LLMClient(Protocol):
    model_name: str

    def generate(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        payload: dict,
        output_model: type[OutputModel],
    ) -> OutputModel: ...


class OpenAIJsonClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model_name = self.settings.openai_model
        self._client = (
            OpenAI(
                api_key=self.settings.openai_api_key,
                timeout=self.settings.openai_timeout_seconds,
            )
            if self.settings.openai_api_key
            else None
        )

    def generate(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        payload: dict,
        output_model: type[OutputModel],
    ) -> OutputModel:
        if not self._client:
            raise AgentProviderError(
                "OpenAI is not configured. Add OPENAI_API_KEY to the local .env file."
            )
        schema = output_model.model_json_schema()
        user_content = json.dumps(payload, default=str, ensure_ascii=True)
        prompt = (
            f"{system_prompt}\n\n"
            "Return one JSON object only. It must conform exactly to this JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=True)}"
        )
        retryer = Retrying(
            stop=stop_after_attempt(self.settings.max_agent_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(
                (AgentProviderError, ValidationError, json.JSONDecodeError)
            ),
            reraise=True,
        )
        try:
            for attempt in retryer:
                with attempt:
                    try:
                        response = self._client.chat.completions.create(
                            model=self.model_name,
                            temperature=0,
                            response_format={"type": "json_object"},
                            messages=[
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": user_content},
                            ],
                        )
                        content = response.choices[0].message.content
                        if not content:
                            raise AgentProviderError(
                                f"{agent_name} returned an empty response"
                            )
                        return output_model.model_validate_json(content)
                    except (AgentProviderError, ValidationError, json.JSONDecodeError):
                        raise
                    except Exception as exc:
                        raise AgentProviderError(
                            f"{agent_name} provider request failed"
                        ) from exc
        except Exception as exc:
            if isinstance(exc, (ValidationError, json.JSONDecodeError, AgentProviderError)):
                raise AgentProviderError(
                    f"{agent_name} could not produce a valid decision"
                ) from exc
            raise AgentProviderError(f"{agent_name} provider request failed") from exc
        raise AgentProviderError(f"{agent_name} did not return a decision")
