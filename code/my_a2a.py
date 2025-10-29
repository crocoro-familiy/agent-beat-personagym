
import httpx
import asyncio
import uuid

from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    AgentCard,
    Part,
    TextPart,
    MessageSendParams,
    Message,
    Role,
    SendMessageRequest,
    SendMessageResponse,
)

async def get_agent_card(url: str) -> AgentCard | None:
    httpx_client = httpx.AsyncClient()
    resolver = A2ACardResolver(httpx_client=httpx_client, base_url=url)
    card: AgentCard | None = await resolver.get_agent_card()
    return card


async def wait_agent_ready(url, timeout=10):
    retry_cnt = 0
    while retry_cnt < timeout:
        retry_cnt += 1
        try:
            card = await get_agent_card(url)
            if card is not None:
                return True
            else:
                print(
                    f"Agent card not available yet..., retrying {retry_cnt}/{timeout}"
                )
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


async def send_message(
    url, message, task_id=None, context_id=None, timeout: float = 120.0 
) -> SendMessageResponse:
    card = await get_agent_card(url)
    
    httpx_client = httpx.AsyncClient(timeout=timeout) 
    
    client = A2AClient(httpx_client=httpx_client, agent_card=card)

    message_id = uuid.uuid4().hex
    params = MessageSendParams(
        message=Message(
            role=Role.user,
            parts=[Part(TextPart(text=message))],
            message_id=message_id,
            task_id=task_id,
            context_id=context_id,
        )
    )

    request = SendMessageRequest(id=uuid.uuid4().hex, params=params)
    
    response = await client.send_message(request=request) 
    return response