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
from urllib.parse import urlparse

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, Message
from a2a.utils import new_agent_text_message

AGENT_DIR = Path(__file__).parent
CODE_DIR = AGENT_DIR.parent
PROJECT_ROOT = CODE_DIR.parent

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from run import select_settings, gen_questions, score_answers, mutate_question_with_llm
from eval_tasks import tasks as default_tasks, settings_list as default_settings_list

SPECIALISTS_DIR = PROJECT_ROOT / "specialists"
QUESTIONS_DIR = PROJECT_ROOT / "specialist_questions"
RUBRICS_DIR = PROJECT_ROOT / "rubrics"

def parse_tags(text: str) -> dict:
    params = {}
    pattern = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(text)
    for tag, value in matches:
        params[tag.lower()] = value.strip()
    return params

class SpecialistRegistry:
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
        persona_lower = persona_text.lower()
        for specialist in self.specialists:
            if any(keyword in persona_lower for keyword in specialist.get("keywords", [])):
                return specialist
        return None

class GreenAgentOrchestrator:
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
            questions_dict = await loop.run_in_executor(None, gen_questions, persona, selected_settings, 5)

        print("Green Agent: Finalized questions.")
        
        task_to_qa = await self._ask_questions_and_get_answers(questions_dict)
        print("Green Agent: Collected all answers from white agent.")
        
        if specialist and "rubrics_path" in specialist:
            rubrics_folder_name = Path(specialist["rubrics_path"]).name
            full_rubrics_path = str(RUBRICS_DIR / rubrics_folder_name)
        else:
            full_rubrics_path = str(RUBRICS_DIR / "general")
        print(f"INFO: Using rubrics from: {full_rubrics_path}")
        scores = await loop.run_in_executor(None, score_answers, persona, task_to_qa, full_rubrics_path)
        print("Green Agent: Scored the answers.")
        
        all_scores_list = []
        if isinstance(scores, dict):
            for task_data in scores.values():
                if isinstance(task_data, dict) and 'scores' in task_data and isinstance(task_data['scores'], list):
                    all_scores_list.extend(task_data['scores'])
        
        numeric_scores = [s for s in all_scores_list if isinstance(s, (int, float))]
        
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
        task_to_qa = {task: [] for task in default_tasks if task in questions_dict}
        
        async with httpx.AsyncClient(base_url=self.white_agent_url, timeout=180.0) as white_agent_client:
            for task_name, question_list in questions_dict.items():
                if task_name not in default_tasks: continue
                for question in question_list:
                    print(f"Green Agent: Sending question for task '{task_name}'...")
                    answer = "Error: No response from agent."
                    
                    payload = {
                        "jsonrpc": "2.0",
                        "method": "message/send",
                        "id": str(uuid4()),
                        "params": {
                            "message": {
                                "messageId": str(uuid4()),
                                "role": "user",
                                "parts": [{"kind": "text", "text": question}]
                            }
                        }
                    }
                    
                    try:
                        response = await white_agent_client.post('/', json=payload)
                        response.raise_for_status() 
                        
                        response_json = response.json()
                        
                        if 'result' in response_json and 'parts' in response_json['result']:
                            answer = response_json['result']['parts'][0].get('text', 'Error: No text in response part')
                            print(f"Green Agent: Received answer: '{answer[:100]}...'")
                        elif 'error' in response_json:
                            answer = f"Error from agent: {response_json['error'].get('message', 'Unknown Error')}"
                            print(f"Green Agent: {answer}")
                        else:
                            print("Green Agent: Received an unknown or empty response.")

                    except httpx.ReadTimeout:
                        answer = "Error: Timed out waiting for White Agent to respond."
                        print(f"Green Agent: {answer}")
                    except Exception as e:
                        answer = f"Error during communication: {e}"
                        print(f"Green Agent: {answer}")
                    
                    task_to_qa.setdefault(task_name, []).append((question, answer))
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
            parsed_params = parse_tags(raw_text)
            white_agent_url = parsed_params.get('white_agent_url')
            if not white_agent_url:
                raise ValueError("Could not extract '<white_agent_url>'...")

            orchestrator = GreenAgentOrchestrator(white_agent_url=white_agent_url, registry=specialist_registry)
            final_scores = await orchestrator.run_full_evaluation()
            result_message = f"Evaluation Complete. Final Scores: {json.dumps(final_scores, indent=2)}"
            await event_queue.enqueue_event(new_agent_text_message(result_message))
        except Exception as e:
            tb_str = traceback.format_exc()
            error_message = f"GREEN AGENT CRASHED:\n{e}\n\nTRACEBACK:\n{tb_str}"
            print(error_message)
            await event_queue.enqueue_event(new_agent_text_message(error_message))
        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise Exception("Cancellation not supported.")

def load_agent_card_toml(card_filename="green_agent_card.toml") -> dict:
    card_path = AGENT_DIR / card_filename
    if not card_path.exists():
         raise FileNotFoundError(f"Cannot find {card_filename} in {AGENT_DIR}")
    with open(card_path, "rb") as f:
        return tomllib.load(f)

def start_green_agent(host="0.0.0.0", port=9999):
    print("Starting PersonaGym Green Agent...")
    try:
        agent_card_toml = load_agent_card_toml()

        # --- 1) Start from function defaults ---
        final_host = host
        final_port = port

        # --- 2) If card has URL, use that as a base ---
        card_url = agent_card_toml.get("url")
        if card_url:
            parsed = urlparse(card_url)
            if parsed.hostname:
                final_host = parsed.hostname
            if parsed.port:
                final_port = parsed.port

        # --- 3) Card host/port fields override previous values (if present) ---
        final_host = agent_card_toml.get("host", final_host)
        final_port = agent_card_toml.get("port", final_port)

        # --- 4) Environment variables override everything (for controller runs) ---
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

        # --- 5) Keep the card in sync with what we will actually use ---
        agent_card_toml["host"] = final_host
        agent_card_toml["port"] = final_port
        agent_card_toml["url"] = f"http://{final_host}:{final_port}/"
        print(f"Agent Card loaded. URL set to {agent_card_toml['url']}")

        request_handler = DefaultRequestHandler(
            agent_executor=GreenAgentExecutor(),
            task_store=InMemoryTaskStore(),
        )
        server = A2AStarletteApplication(
            agent_card=AgentCard(**agent_card_toml),
            http_handler=request_handler,
        )

        print(f"Starting server on {final_host}:{final_port}")
        uvicorn.run(server.build(), host=final_host, port=final_port)

    except FileNotFoundError as e:
        print(f"FATAL ERROR: {e}. Please ensure green_agent_card.toml exists.")
    except Exception as e:
        print(f"FATAL ERROR during startup: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    start_green_agent()