import asyncio
import logging
import math
import os
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field

from agent_framework import Agent, Content, Message
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.activity import ActivityAgentServerHost
from microsoft_agents.activity import Attachment
from microsoft_agents.hosting.core import TurnContext, TurnState
from microsoft_agents.hosting.core.storage import MemoryStorage
from pydantic import ValidationError

from activity_delivery import ActivityDelivery
from agent import CALLER, Caller
from contracts import ChartRequest
from progress import PROGRESS

logger = logging.getLogger(__name__)

ERROR_TEXT = "The chart request failed. Check the agent logs and try again."
TIMEOUT_TEXT = "The chart request timed out. Please narrow the question and try again."


@dataclass(frozen=True)
class ActivityOptions:
    proactive_after: float = 35
    heartbeat_seconds: float = 15
    turn_timeout: float = 100

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) and value > 0 for value in (
            self.proactive_after, self.heartbeat_seconds, self.turn_timeout,
        )):
            raise ValueError("Activity timing options must be finite positive seconds")
        if self.proactive_after > 35 or self.proactive_after >= self.turn_timeout:
            raise ValueError("Activity handoff must be at most 35 seconds and earlier than the turn timeout")
        if self.heartbeat_seconds > 20:
            raise ValueError("Activity heartbeat must be at most 20 seconds")

    @classmethod
    def from_env(cls) -> "ActivityOptions":
        return cls(
            proactive_after=float(os.getenv("ACTIVITY_PROACTIVE_AFTER_SECONDS", "35")),
            heartbeat_seconds=float(os.getenv("ACTIVITY_KEEPALIVE_SECONDS", "15")),
            turn_timeout=float(os.getenv("ACTIVITY_TURN_TIMEOUT_SECONDS", "100")),
        )


@dataclass
class Conversation:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    history: list[dict[str, str]] | None = None
    generation: int = 0
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


class ActivityTurns:
    def __init__(self, agent: Agent, options: ActivityOptions) -> None:
        self.agent = agent
        self.options = options
        self.conversations: OrderedDict[tuple[str, ...], Conversation] = OrderedDict()
        self.tasks: set[asyncio.Task[None]] = set()
        self.closed = False

    def _conversation(self, context: TurnContext) -> Conversation:
        activity = context.activity
        if activity.conversation is None or not activity.conversation.id:
            raise ValueError("Activity requires a conversation id")
        key = (
            activity.service_url or "",
            activity.channel_id.channel if activity.channel_id else "",
            activity.conversation.tenant_id or "",
            activity.conversation.id,
            (activity.recipient.id or "") if activity.recipient else "",
        )
        if key not in self.conversations:
            if len(self.conversations) >= 128:
                idle = next((k for k, v in self.conversations.items() if not v.tasks), None)
                if idle is None:
                    raise RuntimeError("Activity conversation capacity exceeded")
                del self.conversations[idle]
            self.conversations[key] = Conversation()
        self.conversations.move_to_end(key)
        return self.conversations[key]

    async def handle(self, context: TurnContext, state: TurnState) -> None:
        delivery = ActivityDelivery(context)
        slot = self._conversation(context)
        prompt = context.activity.text or ""
        if prompt.strip().lower() == "/clear":
            slot.generation += 1
            slot.history = []
            for task in list(slot.tasks):
                task.cancel()
            await asyncio.gather(*list(slot.tasks), return_exceptions=True)
            async with slot.lock:
                state.conversation.set_value("chart_history", slot.history)
                await state.save(context, force=True)
                await delivery.finish("Cleared this conversation's chart history and cancelled pending charts.", [])
            return
        caller = Caller(output="activity")
        value = context.activity.value
        if isinstance(value, dict) and value.get("action") == "drill":
            try:
                caller.request = ChartRequest.model_validate(value.get("request"))
            except ValidationError:
                logger.warning("Invalid Activity drill-down request")
                await delivery.finish("The drill-down request is invalid. Request a new chart.", [])
                return
            prompt = f"Render this drill-down: {caller.request.model_dump_json()}"
        if not prompt.strip():
            await delivery.finish("Ask for a chart, for example: revenue by region.", [])
            return
        if self.closed or len(self.tasks) >= 32:
            logger.warning("Activity work rejected: shutdown or active-turn capacity reached")
            await delivery.finish("The agent is busy. Please try again shortly.", [])
            return
        generation = slot.generation
        task = asyncio.create_task(self._run(context, state, slot, generation, caller, prompt, delivery))
        self.tasks.add(task)
        slot.tasks.add(task)
        task.add_done_callback(lambda completed: self._completed(completed, slot))
        try:
            done, _ = await asyncio.wait({task}, timeout=self.options.proactive_after)
            if done:
                await task
            elif await delivery.detach():
                logger.info("Activity turn handed over to proactive delivery")
            else:
                logger.info("Activity turn remains attached: completed or no push capability")
                await task
        except asyncio.CancelledError:
            if not delivery.detached:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def _run(
        self, context: TurnContext, state: TurnState, slot: Conversation, generation: int,
        caller: Caller, prompt: str, delivery: ActivityDelivery,
    ) -> None:
        token = CALLER.set(caller)
        progress_token = PROGRESS.set(delivery.status)
        keepalive = asyncio.create_task(delivery.heartbeat(self.options.heartbeat_seconds))
        try:
            async with asyncio.timeout(self.options.turn_timeout):
                await delivery.status(
                    "Finishing the previous request first..." if slot.lock.locked()
                    else "Understanding your chart request..."
                )
                async with slot.lock:
                    if generation != slot.generation:
                        await delivery.finish("This request was cancelled by /clear.", [])
                        return
                    history = list(
                        slot.history if slot.history is not None
                        else state.conversation.get_value("chart_history") or []
                    )
                    messages = [Message(m["role"], [Content.from_text(m["text"])]) for m in history]
                    messages.append(Message("user", [Content.from_text(prompt)]))
                    response = await self.agent.run(messages)
                    if generation != slot.generation:
                        return
                    text = response.text or "Chart ready."
                    attachments = [Attachment(
                        content_type="application/vnd.microsoft.card.adaptive",
                        content=bundle.adaptive_card,
                    ) for bundle in caller.bundles]
                    await delivery.finish(text, attachments)
                    history.extend([
                        {"role": "user", "text": prompt},
                        {"role": "assistant", "text": text + "\n" + "\n".join(
                            b.request.model_dump_json() for b in caller.bundles
                        )},
                    ])
                    slot.history = history[-12:]
                    state.conversation.set_value("chart_history", slot.history)
                    # Detached work finishes after AgentApplication's automatic state save.
                    await state.save(context, force=True)
        except TimeoutError:
            logger.error("Activity chart turn exceeded its work deadline")
            if not delivery.finished:
                await delivery.finish(TIMEOUT_TEXT, [])
        except asyncio.CancelledError:
            if generation != slot.generation:
                if not delivery.finished and not delivery.detached:
                    await delivery.finish("This request was cancelled by /clear.", [])
                return
            raise
        except Exception as error:
            logger.error("Activity chart work or delivery failed (%s)", type(error).__name__)
            if not delivery.finished:
                await delivery.finish(ERROR_TEXT, [])
        finally:
            keepalive.cancel()
            with suppress(asyncio.CancelledError):
                await keepalive
            PROGRESS.reset(progress_token)
            CALLER.reset(token)

    def _completed(self, task: asyncio.Task[None], slot: Conversation) -> None:
        self.tasks.discard(task)
        slot.tasks.discard(task)
        if task.cancelled():
            logger.warning("Activity work cancelled before completion")
        elif (error := task.exception()) is not None:
            logger.error("Activity background delivery failed (%s)", type(error).__name__)

    async def close(self) -> None:
        self.closed = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        self.conversations.clear()


class ChartAgentHost(ActivityAgentServerHost, ResponsesHostServer):
    activity_turns: ActivityTurns


def build_host(agent: Agent) -> ChartAgentHost:
    host = ChartAgentHost(agent=agent, storage=MemoryStorage())
    host.activity_turns = ActivityTurns(agent, ActivityOptions.from_env())
    host.agent_app.activity("message")(host.activity_turns.handle)

    @host.agent_app.error
    async def on_error(context: TurnContext, error: Exception) -> None:
        logger.error("Activity chart request failed (%s)", type(error).__name__)
        await ActivityDelivery(context).finish(ERROR_TEXT, [])

    return host
