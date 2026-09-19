import asyncio
import logging
from contextlib import suppress

from agent_framework import Agent, Content, Message
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.activity import ActivityAgentServerHost
from microsoft_agents.activity import Activity, Attachment
from microsoft_agents.hosting.core import TurnContext, TurnState
from microsoft_agents.hosting.core.storage import MemoryStorage
from pydantic import ValidationError

from agent import CALLER, Caller
from contracts import ChartRequest

logger = logging.getLogger(__name__)


class ChartAgentHost(ActivityAgentServerHost, ResponsesHostServer):
    pass


def build_host(agent: Agent) -> ChartAgentHost:
    host = ChartAgentHost(agent=agent, storage=MemoryStorage())

    @host.agent_app.activity("message")
    async def on_message(context: TurnContext, state: TurnState) -> None:
        prompt = context.activity.text or ""
        if prompt.strip().lower() == "/clear":
            state.conversation.set_value("chart_history", [])
            await context.send_activity("Cleared this conversation's chart history.")
            return
        caller = Caller(output="activity")
        value = context.activity.value
        if isinstance(value, dict) and value.get("action") == "drill":
            try:
                caller.request = ChartRequest.model_validate(value.get("request"))
            except ValidationError:
                await context.send_activity("The drill-down request is invalid. Request a new chart.")
                return
            prompt = f"Render this drill-down: {caller.request.model_dump_json()}"
        if not prompt.strip():
            await context.send_activity("Ask for a chart, for example: revenue by region.")
            return
        history = state.conversation.get_value("chart_history") or []
        messages = [Message(m["role"], [Content.from_text(m["text"])]) for m in history]
        messages.append(Message("user", [Content.from_text(prompt)]))

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(15)
                await context.send_activity(Activity(type="typing"))

        token = CALLER.set(caller)
        keepalive = asyncio.create_task(heartbeat())
        try:
            await context.send_activity(Activity(type="typing"))
            async with asyncio.timeout(100):
                response = await agent.run(messages)
            await context.send_activity(response.text or "Chart ready.")
            for bundle in caller.bundles:
                await context.send_activity(Activity(
                    type="message",
                    attachments=[Attachment(
                        content_type="application/vnd.microsoft.card.adaptive",
                        content=bundle.adaptive_card,
                    )],
                ))
            history.extend([
                {"role": "user", "text": prompt},
                {"role": "assistant", "text": response.text + "\n" + "\n".join(
                    b.request.model_dump_json() for b in caller.bundles
                )},
            ])
            # State is conversation scoped; bounds protect local in-memory demo sessions.
            state.conversation.set_value("chart_history", history[-12:])
        except TimeoutError:
            logger.exception("Activity chart turn timed out")
            await context.send_activity("The chart request timed out. Please narrow the question and try again.")
        finally:
            keepalive.cancel()
            with suppress(asyncio.CancelledError):
                await keepalive
            CALLER.reset(token)

    @host.agent_app.error
    async def on_error(context: TurnContext, error: Exception) -> None:
        logger.error("Activity chart request failed", exc_info=error)
        await context.send_activity("The chart request failed. Check the agent logs and try again.")

    return host
