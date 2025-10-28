# File: agent.py
# Combines logic from green_agent_executor.py and set_green_agent.py
# and adapts to Agent Beat structure.

import uvicorn
import tomllib 
import dotenv
import json
import time
import asyncio
import traceback
import httpx
import re 
from uuid import uuid4
from pathlib import Path
import os
import random
import sys

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, Message
from a2a.utils import new_agent_text_message

# 
# Get the path to the directory containing this agent.py file (code/green_agent/)
AGENT_DIR = Path(__file__).parent
# Get the path to the parent directory (code/)
CODE_DIR = AGENT_DIR.parent
# Get the path to the project root (one level above code/)
PROJECT_ROOT = CODE_DIR.parent

# Add the 'code' directory to sys.path so we can import 'run' and 'eval_tasks'
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

# Import the core logic from your PersonaGym code
# Ensure run.py and eval_tasks.py are accessible (e.g., in the same directory or added to PYTHONPATH)
from run import select_settings, gen_questions, score_answers, mutate_question_with_llm
from eval_tasks import tasks as default_tasks, settings_list as default_settings_list

dotenv.load_dotenv()

# Robust Path Definitions 
CODE_DIR = Path(__file__).parent
PROJECT_ROOT = CODE_DIR.parent

# Define all configuration paths relative to the project root
SPECIALISTS_DIR = PROJECT_ROOT / "specialists"
QUESTIONS_DIR = PROJECT_ROOT / "specialist_questions"
RUBRICS_DIR = PROJECT_ROOT / "rubrics"

# --- Helper Function for Parameter Parsing ---
def parse_tags(text: str) -> dict:
    """
    Parses simple XML-like tags from a string and returns a dictionary.
    Example: Extracts 'value' from '<tag>value</tag>' into {'tag': 'value'}.
    Handles multiple tags. Returns empty dict if no tags found.
    """
    params = {}
    # Regex to find content between tags like <tag>content</tag>
    pattern = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(text)
    for tag, value in matches:
        params[tag.lower()] = value.strip() # Store tag name in lowercase
    return params

# --- Specialist Registry Class ---
class SpecialistRegistry:
    """Discovers and manages all specialist configurations."""
    def __init__(self, specialists_dir: Path):
        self.specialists = []
        self.load_specialists(specialists_dir)

    def load_specialists(self, specialists_dir: Path):
        if not specialists_dir.exists():
            print(f"WARN: Specialists directory not found at {specialists_dir}")
            return
        for file_path in specialists_dir.glob("*.json"):
            with open(file_path, 'r') as f:
                try:
                    specialist_config = json.load(f)
                    self.specialists.append(specialist_config)
                    print(f"INFO: Registered specialist '{specialist_config.get('domain_name', 'Unnamed')}'")
                except json.JSONDecodeError as e:
                    print(f"ERROR: Could not parse {file_path}. Invalid JSON: {e}")

    def find_specialist(self, persona_text: str):
        """Checks a persona for keywords to find a matching specialist."""
        persona_lower = persona_text.lower()
        for specialist in self.specialists:
            if any(keyword in persona_lower for keyword in specialist.get("keywords", [])):
                return specialist
        return None 

# Main Orchestrator Class 
# (Copied directly from green_agent_executor.py, includes fixes)
class GreenAgentOrchestrator:
    """Orchestrates the entire evaluation workflow, choosing between specialist and default modes."""
    def __init__(self, white_agent_url: str, registry: SpecialistRegistry):
        self.white_agent_url = white_agent_url
        self.httpx_client = httpx.AsyncClient(timeout=60.0)
        self.registry = registry

    async def run_full_evaluation(self) -> dict:
        print("Green Agent: Starting evaluation...")
        persona = await self._get_persona_from_profile()
        print(f"Green Agent: Discovered persona: '{persona[:80]}...'")

        loop = asyncio.get_event_loop()
        questions_dict = {}
        specialist = self.registry.find_specialist(persona)

        if specialist:
            print(f"INFO: Specialist '{specialist['domain_name']}' detected.")
            
            specialist_settings = specialist.get("settings_list", default_settings_list)
            selected_settings = await loop.run_in_executor(None, select_settings, persona, specialist_settings)
            
            chosen_setting = random.choice(selected_settings) if selected_settings else "a relevant professional setting"
            print(f"INFO: Selected setting for mutation: '{chosen_setting}'")

            questions_file_path_str = specialist.get("static_questions_file")
            if questions_file_path_str:
                questions_file_name = Path(questions_file_path_str).name
                questions_file_path = QUESTIONS_DIR / questions_file_name
                
                with open(questions_file_path, 'r') as f:
                    question_templates = json.load(f)

                for task, templates in question_templates.items():
                    if templates:
                        chosen_template = random.choice(templates)["template"]
                        final_question = await loop.run_in_executor(None, mutate_question_with_llm, persona, chosen_setting, chosen_template)
                        questions_dict.setdefault(task, []).append(final_question)
        else:
            print("INFO: No specialist detected. Using dynamic question generation.")
            selected_settings = await loop.run_in_executor(None, select_settings, persona, default_settings_list)
            questions_dict = await loop.run_in_executor(None, gen_questions, persona, selected_settings, 1)

        print("Green Agent: Finalized questions.")
        task_to_qa = await self._ask_questions_and_get_answers(questions_dict)
        print("Green Agent: Collected answers from white agent.")

        if specialist and "rubrics_path" in specialist:
            rubrics_folder_name = Path(specialist["rubrics_path"]).name
            full_rubrics_path = str(RUBRICS_DIR / rubrics_folder_name)
        else:
            full_rubrics_path = str(RUBRICS_DIR / "general")

        print(f"INFO: Using rubrics from: {full_rubrics_path}")

        scores = await loop.run_in_executor(None, score_answers, persona, task_to_qa, full_rubrics_path)
        print("Green Agent: Scored the answers.")
        
        numeric_scores = [s for s in scores.values() if isinstance(s, (int, float))]
        persona_score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0
        final_metrics = {'persona_score': persona_score, 'per_task_scores': scores}

        if hasattr(self, 'httpx_client') and self.httpx_client and not self.httpx_client.is_closed:
            await self.httpx_client.aclose()
        print("Green Agent: Evaluation complete.")
        return final_metrics

    async def _get_persona_from_profile(self) -> str:
        profile_url = f"{self.white_agent_url}/profile"
        try:
            response = await self.httpx_client.get(profile_url)
            response.raise_for_status()
            profile_json = response.json()
            return profile_json.get("persona_description", "Error: Persona description not found.")
        except httpx.RequestError as exc:
            print(f"ERROR: HTTP request failed: {exc}")
            return f"Error: Failed to connect to White Agent at {profile_url}"
        except Exception as e:
            print(f"ERROR: Failed to get persona: {e}")
            return "Error: Could not retrieve persona."


    async def _ask_questions_and_get_answers(self, questions_dict: dict) -> dict:
        task_to_qa = {task: [] for task in default_tasks if task in questions_dict} # Initialize only for tasks with questions
        # Use a new client instance or ensure the main one isn't closed prematurely
        async with httpx.AsyncClient(base_url=self.white_agent_url, timeout=120.0) as white_agent_client:
            for task_name, question_list in questions_dict.items():
                if task_name not in default_tasks: continue # Skip if task isn't expected
                for question in question_list:
                    payload = {
                        "jsonrpc": "2.0",
                        "method": "message/send",
                        "id": str(uuid4()),
                        "params": {
                            "message": {
                                "messageId": str(uuid4()), # Correctly include messageId
                                "role": "user",
                                "parts": [{"kind": "text", "text": question}]
                            }
                        }
                    }
                    try:
                        response = await white_agent_client.post('/', json=payload)
                        response.raise_for_status()
                        response_json = response.json()
                        # Safely extract answer text
                        answer = "Error: Malformed response"
                        result = response_json.get('result', {})
                        if result and isinstance(result, dict):
                            parts = result.get('parts', [])
                            if parts and isinstance(parts, list) and len(parts) > 0:
                                answer = parts[0].get('text', 'Error: No text in part')
                        task_to_qa.setdefault(task_name, []).append((question, answer))
                    except httpx.RequestError as exc:
                         print(f"ERROR: Failed to send question for task {task_name}: {exc}")
                         task_to_qa.setdefault(task_name, []).append((question, f"Error: Communication failed - {exc}"))
                    except Exception as e:
                        print(f"ERROR: Unexpected error asking question for task {task_name}: {e}")
                        task_to_qa.setdefault(task_name, []).append((question, f"Error: Unexpected failure - {e}"))
        return task_to_qa

specialist_registry = SpecialistRegistry(SPECIALISTS_DIR)

class GreenAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        try:
            incoming_message: Message = context.message
            raw_text = ""
            if incoming_message.parts:
                part_as_dict = incoming_message.parts[0].model_dump()
                if part_as_dict.get('kind') == 'text':
                    raw_text = part_as_dict.get('text', "")

            # --- Revision: Parse parameters using tags ---
            parsed_params = parse_tags(raw_text)
            white_agent_url = parsed_params.get('white_agent_url') # Tag name in lowercase
            # --- End Revision ---

            if not white_agent_url:
                error_msg = ("Could not extract '<white_agent_url>' "
                             "from the incoming message. Please format "
                             "the request according to the agent card examples.")
                raise ValueError(error_msg)

            # Pass the registry to the orchestrator
            orchestrator = GreenAgentOrchestrator(white_agent_url=white_agent_url, registry=specialist_registry)
            final_scores = await orchestrator.run_full_evaluation()

            result_message = f"Evaluation Complete. Final Scores: {json.dumps(final_scores, indent=2)}"
            await event_queue.enqueue_event(new_agent_text_message(result_message))

        except Exception as e:
            tb_str = traceback.format_exc()
            error_message = f"GREEN AGENT CRASHED:\n{e}\n\nTRACEBACK:\n{tb_str}"
            print(error_message) # Print error to console for easier debugging
            await event_queue.enqueue_event(new_agent_text_message(error_message))

        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise Exception("Cancellation not supported.")


# --- Server Setup Logic (from set_green_agent.py) ---
def load_agent_card_toml(card_filename="green_agent_card.toml") -> dict:
    """Loads agent configuration from the specified .toml file."""
    card_path = Path(__file__).parent / card_filename
    if not card_path.exists():
         card_path = PROJECT_ROOT / card_filename
         if not card_path.exists():
              raise FileNotFoundError(f"Cannot find {card_filename} in code dir or project root.")

    with open(card_path, "rb") as f:
        return tomllib.load(f)

def start_green_agent(host="0.0.0.0", port=9999):
    """Loads configuration, sets up, and starts the A2A server."""
    print("Starting PersonaGym Green Agent...")
    try:
        agent_card_toml = load_agent_card_toml()

        agent_card_toml['url'] = f'http://{host}:{port}/'

        host = agent_card_toml.get('host', host)
        port = agent_card_toml.get('port', port)
        agent_card_toml['url'] = f'http://{host}:{port}/' # Set final URL

        print(f"Agent Card loaded. URL set to {agent_card_toml['url']}")

        request_handler = DefaultRequestHandler(
            agent_executor=GreenAgentExecutor(),
            task_store=InMemoryTaskStore(),
        )

        server = A2AStarletteApplication(
            agent_card=AgentCard(**agent_card_toml),
            http_handler=request_handler,
        )

        print(f"Starting server on {host}:{port}")
        uvicorn.run(server.build(), host=host, port=port)

    except FileNotFoundError as e:
        print(f"FATAL ERROR: {e}. Please ensure green_agent_card.toml exists.")
    except Exception as e:
        print(f"FATAL ERROR during startup: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    start_green_agent()