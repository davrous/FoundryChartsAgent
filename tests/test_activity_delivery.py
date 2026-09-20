import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agent_framework import Agent
from microsoft_agents.activity import Activity, Attachment, ResourceResponse, RoleTypes
from microsoft_agents.hosting.core import TurnContext, TurnState
from microsoft_agents.hosting.core.authorization import ClaimsIdentity
from microsoft_agents.hosting.core.channel_adapter import ChannelAdapter
from microsoft_agents.hosting.core.channel_service_client_factory_base import ChannelServiceClientFactoryBase
from microsoft_agents.hosting.core.storage import MemoryStorage

from activity_bridge import ActivityOptions, ActivityTurns, ERROR_TEXT, TIMEOUT_TEXT
from activity_delivery import ActivityDelivery, HANDOVER_TEXT, ProactiveSender, supports_streaming
from agent import CALLER
from contracts import ChartBundle, ChartRequest
from progress import PROGRESS, report_progress


async def turn(conversation="conversation", *, channel="emulator", mode="normal", text="Chart please",
               storage=None, role=None):
    sent = []

    async def send(context, activities):
        sent.extend(activities)
        return [ResourceResponse(id=f"response-{len(sent)}") for _ in activities]

    adapter = Mock(spec=ChannelAdapter)
    adapter.send_activities = AsyncMock(side_effect=send)
    identity = ClaimsIdentity(
        {"ver": "1.0", "aud": "recipient-app", "appid": "calling-app"}, True, "JWT"
    )
    context = TurnContext(adapter, Activity(
        type="message", id=f"input-{text}", text=text, channel_id=channel,
        delivery_mode=mode, service_url="https://connector.example.invalid",
        conversation={"id": conversation, "tenantId": "tenant"},
        from_property={"id": "user"}, recipient={"id": "bot", "role": role},
    ), identity)
    client = SimpleNamespace(conversations=SimpleNamespace(send_to_conversation=AsyncMock()), close=AsyncMock())
    factory = Mock(spec=ChannelServiceClientFactoryBase)
    factory.create_connector_client = AsyncMock(return_value=client)
    context.turn_state[ChannelAdapter.CHANNEL_SERVICE_FACTORY_KEY] = factory
    context.turn_state[ChannelAdapter.OAUTH_SCOPE_KEY] = "app://calling-app"
    state = TurnState()
    await state.load(context, storage if storage is not None else MemoryStorage())
    return context, state, sent, factory, client


def add_chart():
    caller = CALLER.get()
    assert caller is not None
    caller.bundles.append(ChartBundle(
        id="chart", request=ChartRequest(), title="Chart", summary="Summary", rows=[],
        vega_lite={}, images={"png": "https://example.invalid/chart.png"},
        adaptive_card={"type": "AdaptiveCard", "version": "1.5", "body": [
            {"type": "Image", "url": "https://example.invalid/chart.png"},
        ]}, adaptive_supported=False, fallback_reason="Image fallback",
    ))


def runner(run, *, handoff=0.02, timeout=1):
    agent = Mock(spec=Agent)
    agent.run = AsyncMock(side_effect=run)
    return ActivityTurns(agent, ActivityOptions(
        proactive_after=handoff, heartbeat_seconds=0.01, turn_timeout=timeout,
    ))


async def drain(turns):
    await asyncio.wait_for(asyncio.gather(*list(turns.tasks)), timeout=2)


@pytest.mark.parametrize("channel,mode,role,expected", [
    ("msteams", "normal", None, True),
    ("msteams", "normal", RoleTypes.agentic_identity, False),
    ("msteams", "normal", RoleTypes.agentic_user, False),
    ("webchat", "normal", None, True),
    ("directline", "normal", None, True),
    ("test", "stream", None, True),
    ("emulator", "normal", None, False),
    ("msteams", "expectReplies", None, False),
])
async def test_streaming_matches_sdk_channel_capabilities(channel, mode, role, expected):
    context, *_ = await turn(channel=channel, mode=mode, role=role)
    assert supports_streaming(context) is expected


@pytest.mark.parametrize("channel,mode", [
    ("msteams", "normal"), ("webchat", "normal"), ("directline", "normal"), ("test", "stream"),
])
async def test_real_sdk_stream_delivers_content_before_final_with_all_cards(channel, mode):
    context, _, sent, _, _ = await turn(channel=channel, mode=mode)
    delivery = ActivityDelivery(context)
    cards = [Attachment(content_type="application/vnd.microsoft.card.adaptive", content={"type": "AdaptiveCard"})
             for _ in range(2)]
    await delivery.status("Querying data...")
    await delivery.status("Rendering chart...")
    await delivery.finish("Here are both charts.", cards)
    assert [a.type for a in sent] == ["typing", "typing", "typing", "message"]
    infos = [next(e for e in a.entities if e.type == "streaminfo") for a in sent]
    assert [e.stream_type for e in infos] == ["informative", "informative", "streaming", "final"]
    assert [e.stream_sequence for e in infos] == [1, 2, 3, 4]
    stream_id = infos[0].stream_id or "response-1"
    assert all(e.stream_id == stream_id for e in infos[1:])
    assert sent[-2].text == sent[-1].text
    assert sent[-1].text == "Here are both charts."
    assert sent[-1].attachments == cards
    assert all(not a.attachments for a in sent[:-1])
    with pytest.raises(RuntimeError, match="already"):
        await delivery.finish("Duplicate", cards)


async def test_stream_finalization_waits_for_content_transport_to_complete():
    context, _, sent, _, _ = await turn(mode="stream")
    delivery = ActivityDelivery(context)
    await delivery.status("Rendering chart...")
    content_started, acknowledge = asyncio.Event(), asyncio.Event()
    send = context.adapter.send_activities.side_effect

    async def delayed_send(context, activities):
        if any(e.type == "streaminfo" and e.stream_type == "streaming"
               for a in activities for e in a.entities or []):
            content_started.set()
            await acknowledge.wait()
        return await send(context, activities)

    context.adapter.send_activities.side_effect = delayed_send
    card = Attachment(content_type="application/vnd.microsoft.card.adaptive", content={"type": "AdaptiveCard"})
    final = asyncio.create_task(delivery.finish("Summary", [card]))
    try:
        await asyncio.wait_for(content_started.wait(), timeout=1)
        assert not final.done()
        assert not delivery.stream_context.final_sent
        assert all(a.type == "typing" and not a.attachments for a in sent)
        acknowledge.set()
        await asyncio.wait_for(final, timeout=1)
        assert [e.stream_type for a in sent for e in a.entities if e.type == "streaminfo"] == [
            "informative", "streaming", "final",
        ]
        assert sent[-1].attachments == [card]
    finally:
        acknowledge.set()
        if not final.done():
            final.cancel()
        await asyncio.gather(final, return_exceptions=True)


async def test_non_streaming_progress_and_combined_final():
    context, _, sent, _, _ = await turn()
    delivery = ActivityDelivery(context)
    await delivery.status("Querying data...")
    card = Attachment(content_type="application/vnd.microsoft.card.adaptive", content={})
    await delivery.finish("Summary", [card])
    assert sent[0].text == "Querying data..."
    assert sent[0].input_hint == "ignoringInput"
    assert sent[-1].text == "Summary" and sent[-1].attachments == [card]


@pytest.mark.parametrize("anonymous", [False, True])
async def test_push_uses_fresh_connector_with_original_identity_and_no_reply_to(anonymous):
    context, _, sent, factory, client = await turn()
    if anonymous:
        context.identity.is_authenticated = False
        context.identity.authentication_type = "Anonymous"
    sender = ProactiveSender.capture(context)
    assert sender is not None
    activity = Activity(type="message", text="Done")
    await sender.send(activity)
    factory.create_connector_client.assert_awaited_once_with(
        context, context.identity, context.activity.service_url, "app://calling-app",
        context.identity.get_token_scope(), anonymous,
    )
    factory.create_user_token_client.assert_not_called()
    client.conversations.send_to_conversation.assert_awaited_once_with("conversation", activity)
    client.close.assert_awaited_once()
    assert activity.conversation.id == "conversation"
    assert activity.recipient.id == "user" and activity.from_property.id == "bot"
    assert activity.reply_to_id is None and activity.id is None
    assert not sent


async def test_short_turn_does_not_push_and_persists_history():
    async def run(messages):
        add_chart()
        await report_progress("Rendering chart...")
        return SimpleNamespace(text="Summary")

    context, state, sent, factory, _ = await turn()
    turns = runner(run, handoff=0.5)
    await turns.handle(context, state)
    assert sent[-1].text == "Summary" and len(sent[-1].attachments) == 1
    factory.create_connector_client.assert_not_awaited()
    assert len(state.conversation.get_value("chart_history")) == 2
    assert CALLER.get() is None and PROGRESS.get() is None
    await turns.close()


async def test_long_turn_returns_handoff_then_pushes_once_and_saves_after_http_return():
    release = asyncio.Event()
    storage = MemoryStorage()

    async def run(messages):
        await release.wait()
        await report_progress("Rendering chart...")
        add_chart()
        return SimpleNamespace(text="Final summary")

    context, state, sent, factory, client = await turn(storage=storage)
    turns = runner(run)
    try:
        await turns.handle(context, state)
        assert sent[-1].text == HANDOVER_TEXT and not sent[-1].attachments
        assert not client.conversations.send_to_conversation.called
        count = len(sent)
        await state.save(context)  # The inbound application saves before the worker finishes.
        context.adapter.send_activities.side_effect = AssertionError("Inbound connector is closed")
        release.set()
        await drain(turns)
        assert len(sent) == count
        pushed = client.conversations.send_to_conversation.call_args.args[1]
        assert pushed.text == "Final summary" and len(pushed.attachments) == 1
        assert factory.create_connector_client.await_count == 1
        _, reloaded, *_ = await turn(storage=storage)
        assert len(reloaded.conversation.get_value("chart_history")) == 2
    finally:
        await turns.close()


async def test_handoff_racing_final_serializes_notice_before_push():
    context, _, sent, _, client = await turn(mode="stream")
    delivery = ActivityDelivery(context)
    await delivery.status("Working")
    handoff = asyncio.create_task(delivery.detach())
    await asyncio.sleep(0)
    final = asyncio.create_task(delivery.finish("Result", [Attachment(content_type="test", content={})]))
    assert await handoff
    await final
    assert sent[-1].text == HANDOVER_TEXT
    assert not sent[-1].attachments
    assert client.conversations.send_to_conversation.await_count == 1
    assert client.conversations.send_to_conversation.call_args.args[1].text == "Result"


async def test_finished_turn_does_not_send_handoff():
    context, _, sent, _, client = await turn()
    delivery = ActivityDelivery(context)
    await delivery.finish("Result", [])
    assert not await delivery.detach()
    assert [a.text for a in sent] == ["Result"]
    client.conversations.send_to_conversation.assert_not_awaited()


@pytest.mark.parametrize("fail_during", ["informative", "streaming", "final"])
async def test_sdk_stream_cancellation_is_not_reported_as_success(fail_during):
    context, *_ = await turn(channel="msteams")
    delivery = ActivityDelivery(context)
    send = context.adapter.send_activities.side_effect
    attempted = []

    async def failing_send(context, activities):
        for activity in activities:
            info = next(e for e in activity.entities if e.type == "streaminfo")
            attempted.append(info.stream_type)
            if info.stream_type == fail_during:
                raise PermissionError("403 Forbidden")
        return await send(context, activities)

    context.adapter.send_activities.side_effect = failing_send
    await delivery.status("Working")
    with pytest.raises(RuntimeError, match="cancelled"):
        await delivery.finish("Result", [])
    stages = ["informative", "streaming", "final"]
    assert attempted == stages[:stages.index(fail_during) + 1]
    assert not delivery.stream_context.final_sent


async def test_failed_handoff_notice_does_not_lose_proactive_result(caplog):
    context, _, _, _, client = await turn()
    context.adapter.send_activities.side_effect = ConnectionError("Inbound channel gone")
    delivery = ActivityDelivery(context)
    assert await delivery.detach()
    await delivery.finish("Result", [])
    assert client.conversations.send_to_conversation.await_count == 1
    assert "handoff notice failed" in caplog.text


async def test_missing_push_context_stays_attached_without_claiming_handoff():
    async def run(messages):
        await asyncio.sleep(0.04)
        return SimpleNamespace(text="Result")

    context, state, sent, _, client = await turn()
    del context.turn_state[ChannelAdapter.CHANNEL_SERVICE_FACTORY_KEY]
    turns = runner(run)
    await turns.handle(context, state)
    assert sent[-1].text == "Result"
    assert not any(a.text == HANDOVER_TEXT for a in sent)
    client.conversations.send_to_conversation.assert_not_awaited()
    await turns.close()


async def test_real_chart_service_reports_stages_and_keeps_non_activity_calls_quiet(monkeypatch):
    from artifact_storage import ArtifactStore
    from chart_service import ChartService

    monkeypatch.delenv("MOCK_API_URL", raising=False)
    artifacts = Mock(spec=ArtifactStore)
    artifacts.save = AsyncMock(return_value={
        "png": "https://example.invalid/chart.png", "svg": "https://example.invalid/chart.svg",
    })
    artifacts.close = AsyncMock()
    service = ChartService(artifacts)
    progress = AsyncMock()
    token = PROGRESS.set(progress)
    try:
        await service.schema()
        chart = await service.create(ChartRequest())
        assert chart.rows and chart.images["png"] and chart.adaptive_card
        assert [c.args[0] for c in progress.await_args_list] == [
            "Reading the available sample sales dimensions and filters...",
            "Querying the sample sales data...",
            "Rendering your chart...",
            "Saving the chart images and preparing the Adaptive Card...",
        ]
    finally:
        PROGRESS.reset(token)
        await service.close()
    await report_progress("No Activity caller")
    assert progress.await_count == 4


async def test_expect_replies_cannot_detach_into_a_dead_response_buffer():
    async def run(messages):
        await asyncio.sleep(0.04)
        add_chart()
        return SimpleNamespace(text="Result")

    context, state, sent, factory, _ = await turn(mode="expectReplies")
    turns = runner(run)
    await turns.handle(context, state)
    assert len(context.buffered_reply_activities) == 1
    assert context.buffered_reply_activities[0].attachments
    assert context.buffered_reply_activities[0].text == "Result"
    assert not sent
    factory.create_connector_client.assert_not_awaited()
    await turns.close()


@pytest.mark.parametrize("failure,expected", [("timeout", TIMEOUT_TEXT), ("error", ERROR_TEXT)])
async def test_detached_errors_are_reported_via_push(failure, expected):
    release = asyncio.Event()

    async def run(messages):
        await release.wait()
        raise ValueError("Test data failure")

    context, state, sent, _, client = await turn()
    turns = runner(run, timeout=0.1)
    try:
        await turns.handle(context, state)
        assert sent[-1].text == HANDOVER_TEXT
        if failure == "error":
            release.set()
        await drain(turns)
        assert client.conversations.send_to_conversation.await_count == 1
        assert client.conversations.send_to_conversation.call_args.args[1].text == expected
    finally:
        await turns.close()


async def test_failed_push_is_logged_closed_and_not_retried(caplog):
    release = asyncio.Event()

    async def run(messages):
        await release.wait()
        return SimpleNamespace(text="Result")

    context, state, _, _, client = await turn()
    client.conversations.send_to_conversation.side_effect = ConnectionError("Delivery ambiguous")
    turns = runner(run)
    await turns.handle(context, state)
    release.set()
    await drain(turns)
    assert client.conversations.send_to_conversation.await_count == 1
    client.close.assert_awaited_once()
    assert "ConnectionError" in caplog.text
    await turns.close()


async def test_queued_turn_uses_latest_history_not_its_stale_inbound_snapshot():
    release = asyncio.Event()
    calls = []

    async def run(messages):
        calls.append([m.text for m in messages])
        if len(calls) == 1:
            await release.wait()
        return SimpleNamespace(text=f"Result {len(calls)}")

    storage = MemoryStorage()
    first = await turn(text="First", storage=storage)
    second = await turn(text="Second", storage=storage)
    turns = runner(run)
    try:
        await turns.handle(first[0], first[1])
        await turns.handle(second[0], second[1])
        assert len(calls) == 1
        release.set()
        await drain(turns)
        assert calls[1] == ["First", "Result 1\n", "Second"]
        _, reloaded, *_ = await turn(storage=storage)
        assert len(reloaded.conversation.get_value("chart_history")) == 4
    finally:
        await turns.close()


async def test_conversations_and_progress_contexts_are_isolated():
    seen = []

    async def run(messages):
        await asyncio.sleep(0)
        seen.append((messages[0].text, CALLER.get()))
        await report_progress(f"Processing {messages[0].text}")
        return SimpleNamespace(text=messages[0].text)

    a = await turn("A", text="Private A")
    b = await turn("B", text="Private B")
    turns = runner(run, handoff=0.5)
    await asyncio.gather(turns.handle(a[0], a[1]), turns.handle(b[0], b[1]))
    assert seen[0][1] is not seen[1][1]
    assert not any("Private B" in (v.text or "") for v in a[2])
    assert not any("Private A" in (v.text or "") for v in b[2])
    assert CALLER.get() is None and PROGRESS.get() is None
    await turns.close()


async def test_clear_cancels_detached_jobs_and_prevents_stale_history_or_chart():
    release = asyncio.Event()

    async def run(messages):
        await release.wait()
        return SimpleNamespace(text="Stale result")

    storage = MemoryStorage()
    first = await turn(storage=storage)
    turns = runner(run)
    await turns.handle(first[0], first[1])
    clear = await turn(text="/clear", storage=storage)
    await turns.handle(clear[0], clear[1])
    release.set()
    await drain(turns)
    first[4].conversations.send_to_conversation.assert_not_awaited()
    _, reloaded, *_ = await turn(storage=storage)
    assert reloaded.conversation.get_value("chart_history") == []
    assert "cancelled" in clear[2][-1].text
    await turns.close()


async def test_shutdown_cancels_detached_work():
    cancelled = asyncio.Event()

    async def run(messages):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    context, state, _, _, client = await turn()
    turns = runner(run)
    await turns.handle(context, state)
    await turns.close()
    assert cancelled.is_set()
    assert not turns.tasks and not turns.conversations
    client.conversations.send_to_conversation.assert_not_awaited()


async def test_turn_arriving_during_clear_uses_empty_history_and_is_not_overwritten():
    cancelling = asyncio.Event()
    release_cancellation = asyncio.Event()
    seen = []

    async def run(messages):
        seen.append([m.text for m in messages])
        if messages[-1].text == "Blocked":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release_cancellation.wait()
                raise
        return SimpleNamespace(text=f"Result {messages[-1].text}")

    storage = MemoryStorage()
    turns = runner(run)
    try:
        old = await turn(text="Old", storage=storage)
        await turns.handle(old[0], old[1])
        blocked = await turn(text="Blocked", storage=storage)
        await turns.handle(blocked[0], blocked[1])
        clear = await turn(text="/clear", storage=storage)
        clear_task = asyncio.create_task(turns.handle(clear[0], clear[1]))
        await asyncio.wait_for(cancelling.wait(), 1)
        fresh = await turn(text="Fresh", storage=storage)
        fresh_task = asyncio.create_task(turns.handle(fresh[0], fresh[1]))
        await asyncio.sleep(0)
        release_cancellation.set()
        await asyncio.wait_for(asyncio.gather(clear_task, fresh_task), 1)
        await drain(turns)
        assert seen[-1] == ["Fresh"]
        _, reloaded, *_ = await turn(storage=storage)
        history = reloaded.conversation.get_value("chart_history")
        assert len(history) == 2 and history[0]["text"] == "Fresh"
    finally:
        release_cancellation.set()
        await turns.close()


async def test_active_work_capacity_rejects_without_starting_another_model_run():
    async def run(messages):
        await asyncio.Event().wait()

    turns = runner(run, timeout=5)
    try:
        for i in range(32):
            context, state, *_ = await turn(str(i))
            await turns.handle(context, state)
        context, state, sent, _, _ = await turn("overflow")
        await turns.handle(context, state)
        assert turns.agent.run.await_count == 32
        assert len(turns.tasks) == 32
        assert sent[-1].text == "The agent is busy. Please try again shortly."
    finally:
        await turns.close()


@pytest.mark.parametrize("options", [
    {"proactive_after": 36}, {"proactive_after": 0}, {"turn_timeout": 10},
    {"heartbeat_seconds": 21}, {"turn_timeout": float("inf")}, {"proactive_after": float("nan")},
])
def test_invalid_timing_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        ActivityOptions(**options)
