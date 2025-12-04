import logging
import asyncio
import json
from uuid import uuid4
import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest
from scribe import run_scribe

# Please revise the Agent URL accordingly. 
GREEN_AGENT_URL = 'http://localhost:9999'
WHITE_AGENT_URL = 'http://localhost:8002' 
TOTAL_EPOCHS = 50

async def run_epoch(epoch_index):
    print(f"\n\n================ STARTING EPOCH {epoch_index + 1} ================")
    
    async with httpx.AsyncClient(timeout=600) as httpx_client:
        try:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=GREEN_AGENT_URL)
            green_agent_card = await resolver.get_agent_card()
        except Exception as e:
            print(f"   CRITICAL ERROR: Could not connect to Green Agent at {GREEN_AGENT_URL}")
            print(f"   Details: {e}")
            return

        client = A2AClient(httpx_client=httpx_client, agent_card=green_agent_card)

        print(f"   Sending request to Green Agent...")
        print(f"   Target White Agent: {WHITE_AGENT_URL}")
        
        payload = {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'text', 'text': WHITE_AGENT_URL}],
                'messageId': uuid4().hex,
            },
        }
        request = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(**payload))
        
        try:
            response = await client.send_message(request)
        except Exception as e:
            print(f"   ERROR: Request to Green Agent failed/timed out.")
            print(f"   Details: {e}")
            return
        
        full_response_text = response.model_dump_json(indent=2)
        
        print("\n --- RAW GREEN AGENT RESPONSE (DEBUG) ---")
        
        try:
            response_dict = json.loads(full_response_text)
            text_part = response_dict['result']['parts'][0]['text']
            print(text_part) 
        except:
            print(full_response_text)
            
        print("-------------------------------------------\n")

        # Run Scribe
        print("Passing to Scribe...")
        run_scribe(full_response_text)

if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    
    print(f"STARTING FULL TRAINING LOOP ({TOTAL_EPOCHS} Epochs)")
    
    for i in range(TOTAL_EPOCHS):
        try:
            asyncio.run(run_epoch(i))
        except Exception as e:
            print(f"Epoch {i+1} failed: {e}")
            
    print("\nALL EPOCHS COMPLETE.")