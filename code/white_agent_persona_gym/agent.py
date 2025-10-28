# File: agent.py (in your white_agent directory)
# Combines logic from white_agent_executor.py and set_white_agent.py
# Adapts to Agent Beat structure while keeping .toml loading and /profile endpoint.

import uvicorn
import tomllib # Use tomllib for TOML parsing (standard in Python 3.11+)
import dotenv
import json
import traceback
from uuid import uuid4
from pathlib import Path
import os

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, Message
from a2a.utils import new_agent_text_message

from openai import OpenAI
from starlette.responses import JSONResponse
from starlette.routing import Route

dotenv.load_dotenv()

AGENT_DIR = Path(__file__).parent
CARD_FILENAME = "white_agent_card.toml"

def load_agent_card_toml(card_filename=CARD_FILENAME) -> dict:
    """Loads agent configuration from the specified .toml file."""
    card_path = AGENT_DIR / card_filename
    if not card_path.exists():
        raise FileNotFoundError(f"Cannot find {card_filename} in {AGENT_DIR}")

    with open(card_path, "rb") as f: 
        return tomllib.load(f)

# Core White Agent Logic (from white_agent_executor.py) 
class WhiteAgent:
    """Handles the actual LLM call with the persona."""
    def __init__(self, persona: str, question: str):
        self.persona = persona
        self.question = question

    def invoke(self) -> str:
        try:
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            system_prompt = (
                f"You are acting as: {self.persona}. "
                "You must answer the following question while staying strictly in character."
            )
            completion = client.chat.completions.create(
                model="gpt-4o-mini", 
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self.question}
                ]
            )
            return completion.choices[0].message.content or "Error: Empty response from LLM."
        except Exception as e:
            print(f"ERROR calling OpenAI: {e}")
            return f"Error calling OpenAI: {str(e)}"

class WhiteAgentExecutor(AgentExecutor):
    """The A2A wrapper for the white agent's logic."""
    def __init__(self, persona: str):
        if not persona or not isinstance(persona, str):
            raise ValueError("A valid persona string must be provided to the executor.")
        self.persona = persona
        print(f"White Agent Executor Initialized. Persona: '{self.persona}'")

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        try:
            incoming_message: Message = context.message
            question_text = None
            if incoming_message.parts:
                part_as_dict = incoming_message.parts[0].model_dump()
                if part_as_dict.get('kind') == 'text':
                    question_text = part_as_dict.get('text')

            if not question_text:
                error_msg = "Error: No question text was provided in the request."
                print(f"White Agent: {error_msg}")
                await event_queue.enqueue_event(new_agent_text_message(error_msg))
                return

            print(f"White Agent: Received question: '{question_text[:100]}...'") # Log snippet

            # Instantiate the agent logic class
            agent_logic = WhiteAgent(persona=self.persona, question=question_text)
            result = agent_logic.invoke() # This is a synchronous call

            print(f"White Agent: Sending answer: '{result[:100]}...'") # Log snippet
            await event_queue.enqueue_event(new_agent_text_message(result))

        except Exception as e:
            tb_str = traceback.format_exc()
            error_message = f"WHITE AGENT CRASHED:\n{e}\n\nTRACEBACK:\n{tb_str}"
            print(error_message) # Print error to console
            await event_queue.enqueue_event(new_agent_text_message(error_message))
        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise Exception("Cancellation not supported.")


# Custom Endpoint Logic (from set_white_agent.py) ---
async def get_profile(request):
    """Reads the local TOML file and returns the metadata section."""
    try:
        config = load_agent_card_toml()
        metadata = config.get("metadata", {})
        return JSONResponse(metadata)
    except Exception as e:
        print(f"ERROR reading profile from TOML: {e}")
        return JSONResponse({"error": "Could not load agent profile."}, status_code=500)

# Server Setup Logic (adapted from set_white_agent.py) ---
def start_white_agent(host="0.0.0.0", port=8001):
    """Loads configuration, sets up, and starts the A2A server with /profile."""
    print("Starting PersonaGym White Agent...")
    try:
        agent_card_toml = load_agent_card_toml()

        try:
            persona = agent_card_toml["metadata"]["persona_description"]
        except KeyError:
            raise ValueError(
                f"CRITICAL: {CARD_FILENAME} MUST have [metadata] section "
                "with a 'persona_description' key."
            )

        public_card_data = agent_card_toml.copy()
        public_card_data.pop("metadata", None) 
        agent_card_for_public = AgentCard(**public_card_data)

        host = public_card_data.get('host', host)
        port = public_card_data.get('port', port)
        agent_card_for_public.url = f'http://{host}:{port}/'
        print(f"Agent Card loaded. Persona detected. URL set to {agent_card_for_public.url}")

        agent_executor = WhiteAgentExecutor(persona=persona)

        request_handler = DefaultRequestHandler(
            agent_executor=agent_executor,
            task_store=InMemoryTaskStore(),
        )

        server_app = A2AStarletteApplication(
            agent_card=agent_card_for_public,
            http_handler=request_handler,
        )

        app = server_app.build()
        app.routes.append(Route("/profile", endpoint=get_profile, methods=["GET"]))
        print("Added custom /profile endpoint.")

        print(f"Starting server on {host}:{port}")
        uvicorn.run(app, host=host, port=port)

    except FileNotFoundError as e:
        print(f"FATAL ERROR: {e}. Please ensure {CARD_FILENAME} exists.")
    except ValueError as e:
         print(f"FATAL ERROR: Configuration error - {e}")
    except Exception as e:
        print(f"FATAL ERROR during startup: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    start_white_agent()