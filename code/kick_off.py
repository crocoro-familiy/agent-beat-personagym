import logging
from typing import Any
from uuid import uuid4
import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest, AgentCard

async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # The URL of the GREEN agent
    green_agent_url = 'http://localhost:9999'
    
    # The URL of the WHITE agent
    white_agent_url_to_test = 'http://localhost:8001'

    async with httpx.AsyncClient(timeout=None) as httpx_client:
        
        # --- Discover the Green Agent by fetching its card ---
        try:
            logger.info(f"Attempting to fetch Green Agent's card from {green_agent_url}")
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=green_agent_url)
            green_agent_card: AgentCard = await resolver.get_agent_card()
            logger.info("Successfully fetched Green Agent's card.")
        except Exception as e:
            logger.error(f"CRITICAL ERROR: Could not fetch the Green Agent's card. Is the server running? Details: {e}", exc_info=True)
            return

        # --- Initialize the client with the fetched card ---
        client = A2AClient(httpx_client=httpx_client, agent_card=green_agent_card)
        logger.info(f"Client initialized to talk to Green Agent at {green_agent_card.url}")

        # The payload now sends the URL of the white agent.
        send_message_payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [
                    {'kind': 'text', 'text': white_agent_url_to_test}
                ],
                'messageId': uuid4().hex,
            },
        }
        
        request = SendMessageRequest(
            id=str(uuid4()), params=MessageSendParams(**send_message_payload)
        )

        logger.info(f"Sending request to Green Agent: 'Please evaluate the agent at {white_agent_url_to_test}'")
        response = await client.send_message(request)
        
        print("\n--- RESPONSE FROM GREEN AGENT ---")
        # Use .model_dump_json for a nicely formatted string output
        print(response.model_dump_json(indent=2, exclude_none=True))
        print("--- END OF RESPONSE ---\n")

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())