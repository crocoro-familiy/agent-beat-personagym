
import uvicorn
import tomllib 
import dotenv
import json
import traceback
from uuid import uuid4
from pathlib import Path
import os
import asyncio 

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, Message
from a2a.utils import new_agent_text_message

from openai import AsyncOpenAI  
from starlette.responses import JSONResponse
from starlette.routing import Route

AGENT_DIR = Path(__file__).parent
CODE_DIR = AGENT_DIR.parent
PROJECT_ROOT = CODE_DIR.parent
dotenv.load_dotenv(PROJECT_ROOT / ".env") 

CARD_FILENAME = "white_agent_card.toml"

def load_agent_card_toml(card_filename=CARD_FILENAME) -> dict:
    card_path = AGENT_DIR / card_filename
    if not card_path.exists():
        raise FileNotFoundError(f"Cannot find {card_filename} in {AGENT_DIR}")
    with open(card_path, "rb") as f: 
        return tomllib.load(f)

class WhiteAgent:
    def __init__(self, persona: str, question: str):
        self.persona = persona
        self.question = question
        self.client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    async def invoke(self) -> str:
        try:
            system_prompt = (
                f"You are acting as: {self.persona}. "
                "You must answer the following question while staying strictly in character."
            )
            completion = await self.client.chat.completions.create(
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
    def __init__(self, persona: str):
        self.persona = persona
        print(f"White Agent Executor Initialized. Persona: '{self.persona[:50]}...'")

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
                await event_queue.enqueue_event(new_agent_text_message(error_msg))
                return

            print(f"White Agent: Received question: '{question_text[:100]}...'")

            agent_logic = WhiteAgent(persona=self.persona, question=question_text)
            
            print(f"White Agent: Awaiting response from OpenAI...")
            result = await agent_logic.invoke()
            
            print(f"White Agent: Received answer. Sending: '{result[:100]}...'")
            await event_queue.enqueue_event(new_agent_text_message(result))

        except Exception as e:
            tb_str = traceback.format_exc()
            error_message = f"WHITE AGENT CRASHED:\n{e}\n\nTRACEBACK:\n{tb_str}"
            print(error_message)
            await event_queue.enqueue_event(new_agent_text_message(error_message))
        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise Exception("Cancellation not supported.")

async def get_profile(request):
    try:
        config = load_agent_card_toml()
        metadata = config.get("metadata", {})
        return JSONResponse(metadata)
    except Exception as e:
        print(f"ERROR reading profile from TOML: {e}")
        return JSONResponse({"error": "Could not load agent profile."}, status_code=500)

def start_white_agent(host="0.0.0.0", port=8001):
    print("Starting PersonaGym White Agent...")
    try:
        agent_card_toml = load_agent_card_toml()
        dotenv.load_dotenv()

        # Persona is required in metadata 
        try:
            persona = agent_card_toml["metadata"]["persona_description"]
        except KeyError:
            raise ValueError(
                f"CRITICAL: {CARD_FILENAME} MUST have [metadata] section "
                "with a 'persona_description' key."
            )

        # Start from function defaults 
        final_host = host
        final_port = port

        # If card has URL, use that as a base 
        card_url = agent_card_toml.get("url")
        if card_url:
            parsed = urlparse(card_url)
            if parsed.hostname:
                final_host = parsed.hostname
            if parsed.port:
                final_port = parsed.port

        # Card host/port fields override previous values (if present) 
        final_host = agent_card_toml.get("host", final_host)
        final_port = agent_card_toml.get("port", final_port)

        # Environment variables override everything
        env_host = os.getenv("HOST")
        env_port = os.getenv("AGENT_PORT")

        if env_host:
            print(f"[INFO] Using HOST from environment: {env_host}")
            final_host = env_host

        if env_port:
            try:
                final_port = int(env_port)
                print(f"[INFO] Using AGENT_PORT from environment: {final_port}")
            except ValueError:
                print(f"[WARN] Invalid AGENT_PORT={env_port!r}, keeping {final_port}")

        # Keep the card in sync with what we will actually use 
        agent_card_toml["host"] = final_host
        agent_card_toml["port"] = final_port

        env_agent_url = os.getenv("AGENT_URL")
        if env_agent_url:
            print(f"[INFO] Using AGENT_URL from environment: {env_agent_url}")
            agent_card_toml["url"] = env_agent_url
        else:
            agent_card_toml["url"] = f"http://{final_host}:{final_port}/"
            print("[INFO] Using INTERNAL_URL from AGENTCARD")

        # Build the *public* card (no metadata) from the resolved config
        public_card_data = agent_card_toml.copy()
        public_card_data.pop("metadata", None)
        agent_card_for_public = AgentCard(**public_card_data)

        print(
            "Agent Card loaded. Persona detected. "
            f"URL set to {agent_card_for_public.url}"
        )

        # Build executor and server app 
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

        print(f"Starting server on {final_host}:{final_port}")
        uvicorn.run(app, host=final_host, port=final_port)

    except FileNotFoundError as e:
        print(f"FATAL ERROR: {e}. Please ensure {CARD_FILENAME} exists.")
    except ValueError as e:
        print(f"FATAL ERROR: Configuration error - {e}")
    except Exception as e:
        print(f"FATAL ERROR during startup: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    start_white_agent()