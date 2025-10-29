#Issac: This debug_kickoff.py is only used for debug.
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
CODE_DIR = PROJECT_ROOT / "code"
MY_UTIL_DIR = CODE_DIR / "my_util"

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
if str(MY_UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(MY_UTIL_DIR))

try:
    from my_a2a import send_message, wait_agent_ready
except ImportError as e:
    print(f"ERROR: Could not import from my_a2a.py: {e}")
    print("Please ensure my_a2a.py is in code/my_util/ and is correct.")
    sys.exit(1)

GREEN_URL = 'http://localhost:9999'
WHITE_URL = 'http://localhost:8001'

async def main():
    print("--- DEBUG KICKOFF ---")
    
    print(f"Waiting for Green Agent at {GREEN_URL}...")
    if not await wait_agent_ready(GREEN_URL, timeout=20):
        print("CRITICAL: Green Agent not found. (Did you start it in Terminal 2?)")
        return
        
    print(f"Waiting for White Agent at {WHITE_URL}...")
    if not await wait_agent_ready(WHITE_URL, timeout=20):
        print("CRITICAL: White Agent not found. (Did you start it in Terminal 1?)")
        return
    print("Agents are ready.")

    initial_message_text = f"""
Start PersonaGym evaluation for the agent located at:
<white_agent_url>
{WHITE_URL}
</white_agent_url>
"""
    print("Sending evaluation task to Green Agent... This may take up to 15 minutes.")

    send_response = await send_message(
        url=GREEN_URL,
        message=initial_message_text,
        task_id=None,
        timeout=900.0  
    )
    
    print("--- FINAL RESPONSE FROM GREEN AGENT ---")
    
    try:
        if hasattr(send_response, 'error') and send_response.error:
            print(f"ERROR received from agent: {send_response.error.message}")
        elif hasattr(send_response, 'result') and send_response.result and send_response.result.parts:
            final_text = send_response.result.parts[0].text
            print(final_text)
        else:
            print("Received unknown response:")
            print(send_response.model_dump_json(indent=2))
            
    except Exception as e:
        print(f"Could not parse response: {e}")
        print(send_response.model_dump_json(indent=2))
        
    print("--- DEBUG KICKOFF FINISHED ---")

if __name__ == "__main__":
    asyncio.run(main())