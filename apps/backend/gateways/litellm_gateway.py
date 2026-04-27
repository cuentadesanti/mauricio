import os

import litellm
from langfuse.decorators import langfuse_context, observe

from ..core.config import settings
from ..domain.model_gateway import CompletionRequest, CompletionResponse

# wire Langfuse a LiteLLM globalmente: cada llamada se traza sola
os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
os.environ["LANGFUSE_HOST"] = settings.langfuse_host
litellm.success_callback = ["langfuse"]
litellm.failure_callback = ["langfuse"]


class LiteLLMGateway:
    """Impl concreta de ModelGateway. Único punto que toca litellm."""

    @observe(as_type="generation")
    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        model = req.model_hint or settings.default_model

        kwargs = dict(
            model=model,
            messages=req.messages,
            tools=req.tools,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            metadata={"trace_metadata": req.metadata},
        )
        if req.response_format:
            kwargs["response_format"] = req.response_format

        response = await litellm.acompletion(**kwargs)
        choice = response.choices[0]
        usage = (
            response.usage.model_dump()
            if hasattr(response.usage, "model_dump")
            else dict(response.usage)
        )

        return CompletionResponse(
            content=choice.message.content or "",
            tool_calls=[tc.model_dump() for tc in (choice.message.tool_calls or [])],
            model_used=response.model,
            usage=usage,
            trace_id=langfuse_context.get_current_trace_id() or "",
        )

    async def stream(self, req: CompletionRequest):
        model = req.model_hint or settings.default_model
        kwargs = dict(
            model=model,
            messages=req.messages,
            tools=req.tools,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=True,
            metadata={"trace_metadata": req.metadata},
        )
        if req.response_format:
            kwargs["response_format"] = req.response_format
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            yield chunk
