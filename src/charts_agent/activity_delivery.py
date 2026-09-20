import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, runtime_checkable

from microsoft_agents.activity import Activity, Attachment, Channels, DeliveryModes, ResourceResponse, StreamInfo
from microsoft_agents.hosting.core import TurnContext
from microsoft_agents.hosting.core.app.streaming.streaming_response import StreamingResponse
from microsoft_agents.hosting.core.channel_adapter import ChannelAdapter
from microsoft_agents.hosting.core.channel_service_client_factory_base import ChannelServiceClientFactoryBase

logger = logging.getLogger(__name__)

HANDOVER_TEXT = (
    "This is taking a little longer. I'll keep working in the background "
    "and send the chart here when it is ready."
)


@runtime_checkable
class ConnectorFactory(ChannelServiceClientFactoryBase, Protocol):
    pass


class StreamContext(TurnContext):
    """Observe transport results, including errors the streaming SDK consumes."""

    def __init__(self, context: TurnContext) -> None:
        super().__init__(context)
        self.source = context
        self.send_error: Exception | None = None
        self.final_sent = False

    async def send_activities(self, activities: list[Activity]) -> list[ResourceResponse]:
        try:
            responses = await self.source.send_activities(activities)
        except Exception as error:
            self.send_error = error
            raise
        for activity in activities:
            for entity in activity.entities or []:
                if isinstance(entity, StreamInfo):
                    logger.info(
                        "Activity stream sent: type=%s sequence=%s attachments=%d",
                        entity.stream_type, entity.stream_sequence, len(activity.attachments or []),
                    )
                    if entity.stream_type == "final":
                        self.final_sent = True
        return responses

    def raise_for_send_error(self) -> None:
        if self.send_error is not None:
            raise RuntimeError("The channel cancelled or failed the stream") from self.send_error


def supports_streaming(context: TurnContext) -> bool:
    activity = context.activity
    if activity.delivery_mode == DeliveryModes.expect_replies:
        return False
    channel = activity.channel_id.channel if activity.channel_id else None
    if channel == Channels.ms_teams:
        return not activity.is_agentic_request()
    return channel in (Channels.webchat, Channels.direct_line) or activity.delivery_mode == DeliveryModes.stream


@dataclass
class ProactiveSender:
    context: TurnContext
    factory: ChannelServiceClientFactoryBase
    audience: str

    @classmethod
    def capture(cls, context: TurnContext) -> "ProactiveSender | None":
        if context.activity.delivery_mode == DeliveryModes.expect_replies:
            return None
        factory = context.turn_state.get(ChannelAdapter.CHANNEL_SERVICE_FACTORY_KEY)
        audience = context.turn_state.get(ChannelAdapter.OAUTH_SCOPE_KEY)
        if (not isinstance(factory, ConnectorFactory) or context.identity is None or not isinstance(audience, str)
                or not audience or not context.activity.service_url):
            return None
        return cls(context, factory, audience)

    async def send(self, activity: Activity) -> None:
        identity = self.context.identity
        if identity is None:
            raise RuntimeError("Proactive delivery requires the captured inbound identity")
        reference = self.context.activity.get_conversation_reference()
        if reference.conversation is None or not reference.conversation.id or not reference.service_url:
            raise RuntimeError("Proactive delivery requires the captured conversation reference")
        # The inbound adapter closes its connector when the HTTP turn returns.
        # Mirror its authenticated factory call, not the unrelated user-token/OAuth continuation path.
        client = await self.factory.create_connector_client(
            self.context, identity, reference.service_url, self.audience,
            identity.get_token_scope(),
            not identity.is_authenticated and identity.authentication_type == "Anonymous",
        )
        try:
            activity.apply_conversation_reference(reference)
            activity.id = None
            activity.reply_to_id = None
            await client.conversations.send_to_conversation(reference.conversation.id, activity)
        finally:
            await client.close()


class ActivityDelivery:
    """One final response owns both the summary and every chart attachment."""

    def __init__(self, context: TurnContext) -> None:
        self.context = context
        self.stream_context = StreamContext(context)
        self.stream = StreamingResponse(self.stream_context) if supports_streaming(context) else None
        self.push = ProactiveSender.capture(context)
        self.lock = asyncio.Lock()
        self.detached = False
        self.finished = False
        self.last_status = "Understanding your chart request..."
        self.last_sent = monotonic()
        if self.stream is not None:
            self.stream.set_generated_by_ai_label(True)
        logger.info(
            "Activity delivery: channel=%s mode=%s streaming=%s push_available=%s",
            context.activity.channel_id, context.activity.delivery_mode, self.stream is not None, self.push is not None,
        )

    async def status(self, text: str) -> None:
        async with self.lock:
            self.last_status = text
            if self.finished or self.detached:
                return
            if self.stream is not None:
                self.stream.queue_informative_update(text)
                await self.stream.wait_for_queue()
            elif self.context.activity.delivery_mode != DeliveryModes.expect_replies:
                await self.context.send_activity(Activity(type="message", text=text, input_hint="ignoringInput"))
            self.last_sent = monotonic()

    async def heartbeat(self, seconds: float) -> None:
        try:
            while True:
                await asyncio.sleep(seconds)
                if self.finished or self.detached:
                    return
                if monotonic() - self.last_sent >= seconds:
                    await self.status(self.last_status)
        except Exception as error:
            logger.warning("Activity keepalive failed (%s); chart work continues", type(error).__name__)

    async def _live_final(self, text: str, attachments: list[Attachment]) -> None:
        if self.stream is not None:
            self.stream_context.raise_for_send_error()
            self.stream.set_attachments(attachments)
            self.stream.queue_text_chunk(text)
            # end_stream() coalesces an undrained text chunk into "final".
            # Deliver the "streaming" content phase before finalizing with cards.
            await self.stream.wait_for_queue()
            self.stream_context.raise_for_send_error()
            await self.stream.end_stream()
            # The SDK treats a Teams 403 as stream cancellation rather than raising.
            if not self.stream_context.final_sent:
                raise RuntimeError("The channel cancelled the stream before final delivery")
        else:
            await self.context.send_activity(Activity(type="message", text=text, attachments=attachments))

    async def finish(self, text: str, attachments: list[Attachment]) -> None:
        async with self.lock:
            if self.finished:
                raise RuntimeError("The Activity response has already been finalized")
            # A failed send may already have reached the channel; never retry the final blindly.
            self.finished = True
            if self.detached:
                if self.push is None:
                    raise RuntimeError("Detached Activity response has no proactive sender")
                await self.push.send(Activity(type="message", text=text, attachments=attachments))
            else:
                await self._live_final(text, attachments)

    async def detach(self) -> bool:
        async with self.lock:
            if self.finished or self.detached or self.push is None:
                return False
            # Serialize handoff with final delivery so nothing targets a closing stream.
            self.detached = True
            try:
                await self._live_final(HANDOVER_TEXT, [])
            except Exception as error:
                logger.warning("Live handoff notice failed (%s); continuing with push delivery", type(error).__name__)
            return True
