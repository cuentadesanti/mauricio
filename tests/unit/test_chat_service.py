import json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.domain.chat import ChatMode
from backend.domain.model_gateway import CompletionResponse
from backend.services.chat_service import MAX_TOOL_LOOPS, ChatService


def _resp(content="hello", tool_calls=None):
    return CompletionResponse(
        content=content,
        tool_calls=tool_calls or [],
        model_used="claude-haiku-4-5",
        usage={"total_tokens": 10},
        trace_id="trace-test",
    )


def _mock_repo():
    repo = AsyncMock()
    repo.get_or_create_user.return_value = MagicMock(id="user-1")
    repo.log_event.return_value = None
    repo.create_chat.return_value = MagicMock(id="chat-1", signature=None)
    repo.find_chat_by_signature.return_value = None
    repo.add_message.return_value = MagicMock(id="msg-1")
    repo.update_chat_signature.return_value = None
    repo.get_summary.return_value = None
    return repo


def _patch_service(service: ChatService) -> ChatService:
    """Replace Phase-2 sub-services with no-op mocks so tests stay unit-level."""
    service.memory = AsyncMock()
    service.memory.retrieve_relevant.return_value = []
    service.knowledge = AsyncMock()
    service.knowledge.search.return_value = []
    service._post_turn_jobs = AsyncMock()
    return service


async def _run(service, mode, messages, session=None):
    chunks = []
    async for chunk in service.handle(
        session or AsyncMock(),
        user_handle="test",
        channel="web",
        mode=mode,
        incoming_messages=messages,
    ):
        chunks.append(chunk)
    return chunks


async def test_memoryless_streams_sse_chunks():
    gateway = AsyncMock()
    gateway.complete.return_value = _resp("hola mundo")

    with patch("backend.services.chat_service.Repository") as MockRepo:
        MockRepo.return_value = _mock_repo()
        service = _patch_service(ChatService(gateway=gateway))
        chunks = await _run(service, ChatMode.MEMORYLESS, [{"role": "user", "content": "hi"}])

    full = "".join(chunks)
    assert "hola mundo" in full
    assert chunks[0].startswith("data:")
    assert chunks[-1].strip() == "data: [DONE]"


async def test_memoryless_does_not_persist_chat():
    gateway = AsyncMock()
    gateway.complete.return_value = _resp("ok")

    mock_repo = _mock_repo()
    with patch("backend.services.chat_service.Repository") as MockRepo:
        MockRepo.return_value = mock_repo
        service = _patch_service(ChatService(gateway=gateway))
        await _run(service, ChatMode.MEMORYLESS, [{"role": "user", "content": "hi"}])

    mock_repo.create_chat.assert_not_called()
    mock_repo.add_message.assert_not_called()


async def test_persistent_creates_chat_on_first_message():
    gateway = AsyncMock()
    gateway.complete.return_value = _resp("respuesta")

    mock_repo = _mock_repo()
    with patch("backend.services.chat_service.Repository") as MockRepo, \
         patch("backend.services.chat_service.asyncio.create_task"):
        MockRepo.return_value = mock_repo
        service = _patch_service(ChatService(gateway=gateway))
        await _run(service, ChatMode.PERSISTENT, [{"role": "user", "content": "hola"}])

    mock_repo.create_chat.assert_called_once()
    mock_repo.add_message.assert_called()


async def test_persistent_reuses_existing_chat():
    gateway = AsyncMock()
    gateway.complete.return_value = _resp("respuesta")

    existing_chat = MagicMock(id="chat-existing", signature="old-sig")
    mock_repo = _mock_repo()
    mock_repo.find_chat_by_signature.return_value = existing_chat

    with patch("backend.services.chat_service.Repository") as MockRepo, \
         patch("backend.services.chat_service.asyncio.create_task"):
        MockRepo.return_value = mock_repo
        service = _patch_service(ChatService(gateway=gateway))
        await _run(
            service,
            ChatMode.PERSISTENT,
            [
                {"role": "user", "content": "hola"},
                {"role": "assistant", "content": "hola!"},
                {"role": "user", "content": "¿cómo estás?"},
            ],
        )

    mock_repo.create_chat.assert_not_called()


async def test_tool_call_is_executed_and_result_forwarded():
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "time_now", "arguments": "{}"},
    }
    gateway = AsyncMock()
    gateway.complete.side_effect = [
        _resp("", tool_calls=[tool_call]),
        _resp("Son las 12:00"),
    ]

    with patch("backend.services.chat_service.Repository") as MockRepo:
        MockRepo.return_value = _mock_repo()
        service = _patch_service(ChatService(gateway=gateway))
        chunks = await _run(
            service, ChatMode.MEMORYLESS, [{"role": "user", "content": "¿qué hora es?"}]
        )

    assert gateway.complete.call_count == 2
    second_call_msgs = gateway.complete.call_args_list[1][0][0].messages
    tool_msgs = [m for m in second_call_msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0]["content"])
    assert "iso" in payload

    full = "".join(chunks)
    assert "12:00" in full


async def test_unknown_tool_returns_error_message():
    tool_call = {
        "id": "call_2",
        "type": "function",
        "function": {"name": "does_not_exist", "arguments": "{}"},
    }
    gateway = AsyncMock()
    gateway.complete.side_effect = [
        _resp("", tool_calls=[tool_call]),
        _resp("no puedo"),
    ]

    with patch("backend.services.chat_service.Repository") as MockRepo:
        MockRepo.return_value = _mock_repo()
        service = _patch_service(ChatService(gateway=gateway))
        await _run(service, ChatMode.MEMORYLESS, [{"role": "user", "content": "usa X"}])

    second_call_msgs = gateway.complete.call_args_list[1][0][0].messages
    tool_result = next(m for m in second_call_msgs if m["role"] == "tool")
    error = json.loads(tool_result["content"])["error"]
    assert "does_not_exist" in error


async def test_max_tool_loops_halts_and_returns_sentinel():
    tool_call = {
        "id": "call_loop",
        "type": "function",
        "function": {"name": "time_now", "arguments": "{}"},
    }
    gateway = AsyncMock()
    gateway.complete.return_value = _resp("", tool_calls=[tool_call])

    with patch("backend.services.chat_service.Repository") as MockRepo:
        MockRepo.return_value = _mock_repo()
        service = _patch_service(ChatService(gateway=gateway))
        chunks = await _run(service, ChatMode.MEMORYLESS, [{"role": "user", "content": "loop"}])

    assert gateway.complete.call_count == MAX_TOOL_LOOPS
    full = "".join(chunks)
    assert "stopped" in full
