from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Message
from a2a.utils import new_agent_text_message
import asyncio
import traceback
import httpx
import json
from uuid import uuid4
from pathlib import Path
import os
import random

# Import the core logic from your PersonaGym code
from run import select_settings, gen_questions, score_answers, mutate_question_with_llm
from eval_tasks import tasks as default_tasks, settings_list as default_settings_list

# --- New Specialist Registry Class ---
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
                specialist_config = json.load(f)
                self.specialists.append(specialist_config)
                print(f"INFO: Registered specialist '{specialist_config['domain_name']}'")

    def find_specialist(self, persona_text: str):
        persona_lower = persona_text.lower()
        for specialist in self.specialists:
            if any(keyword in persona_lower for keyword in specialist["keywords"]):
                return specialist
        return None

# --- Main Orchestrator Class (Revised) ---
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

        # Check for a specialist
        specialist = self.registry.find_specialist(persona)

        if specialist:
            # --- Specialist Workflow ---
            print(f"INFO: Specialist '{specialist['domain_name']}' detected.")
            
            # Use the specialist's curated settings list
            specialist_settings = specialist.get("settings_list", default_settings_list)
            print("INFO: Selecting a setting from the specialist's list...")
            selected_settings = await loop.run_in_executor(None, select_settings, persona, specialist_settings)
            
            # Load question templates and mutate them
            print("INFO: Mutating static question templates...")
            questions_file = Path(__file__).parent / specialist["static_questions_file"]
            with open(questions_file, 'r') as f:
                question_templates = json.load(f)

            # Randomly pick one setting from the list returned by the LLM
            chosen_setting = random.choice(selected_settings) if selected_settings else "a relevant professional setting"

            for task, templates in question_templates.items():
                if templates:
                    # Randomly pick one template per task for this run
                    chosen_template = random.choice(templates)["template"]

                    # LLM will further mutate the question to a new question. 
                    final_question = await loop.run_in_executor(
                        None, 
                        mutate_question_with_llm, 
                        persona, 
                        selected_settings, 
                        chosen_template
                    )
                    questions_dict.setdefault(task, []).append(final_question)
        else:
            # --- Default Dynamic Workflow ---
            print("INFO: No specialist detected. Using dynamic question generation.")
            selected_settings = await loop.run_in_executor(None, select_settings, persona, default_settings_list)
            questions_dict = await loop.run_in_executor(None, gen_questions, persona, selected_settings, 5)

        print("Green Agent: Finalized questions.")

        task_to_qa = await self._ask_questions_and_get_answers(questions_dict)
        print("Green Agent: Collected answers from white agent.")

        scores = await loop.run_in_executor(None, score_answers, persona, task_to_qa)
        print("Green Agent: Scored the answers.")
        
        persona_score = sum(scores.values()) / len(scores) if scores else 0
        final_metrics = {'persona_score': persona_score, 'per_task_scores': scores}

        await self.httpx_client.aclose()
        print("Green Agent: Evaluation complete.")
        return final_metrics

    async def _get_persona_from_profile(self) -> str:
        # Issac: This method remains unchanged
        profile_url = f"{self.white_agent_url}/profile"
        response = await self.httpx_client.get(profile_url)
        response.raise_for_status()
        profile_json = response.json()
        return profile_json["persona_description"]

    async def _ask_questions_and_get_answers(self, questions_dict: dict) -> dict:
        #  Issac: This method remains unchanged
        task_to_qa = {task: [] for task in default_tasks}
        white_agent_client = httpx.AsyncClient(base_url=self.white_agent_url, timeout=120.0)

        for task_name, question_list in questions_dict.items():
            for question in question_list:
                payload = { "jsonrpc": "2.0", "method": "message/send", "id": str(uuid4()),
                    "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": question}], "messageId": str(uuid4())}}}
                response = await white_agent_client.post('/', json=payload)
                response.raise_for_status() 
                response_json = response.json()
                answer = response_json.get('result', {}).get('parts', [{}])[0].get('text', 'Error: No answer found in response')
                task_to_qa[task_name].append((question, answer))
        
        await white_agent_client.aclose()
        return task_to_qa

# --- Main Executor Class (Revised to pass the registry) ---

# Initialize the registry once when the module is loaded
SPECIALISTS_DIR = Path(__file__).parent / "specialists"
specialist_registry = SpecialistRegistry(SPECIALISTS_DIR)

class GreenAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        try:
            incoming_message: Message = context.message
            white_agent_url = None
            if incoming_message.parts:
                part_as_dict = incoming_message.parts[0].model_dump()
                if part_as_dict.get('kind') == 'text':
                    white_agent_url = part_as_dict.get('text')
            
            if not white_agent_url:
                raise ValueError("Could not extract the white agent's URL from the request.")
            
            # Pass the initialized registry to the orchestrator
            orchestrator = GreenAgentOrchestrator(white_agent_url=white_agent_url, registry=specialist_registry)
            final_scores = await orchestrator.run_full_evaluation()
            
            result_message = f"Evaluation Complete. Final Scores: {json.dumps(final_scores, indent=2)}"
            await event_queue.enqueue_event(new_agent_text_message(result_message))

        except Exception as e:
            tb_str = traceback.format_exc()
            error_message = f"GREEN AGENT CRASHED:\\n{e}\\n\\nTRACEBACK:\\n{tb_str}"
            await event_queue.enqueue_event(new_agent_text_message(error_message))
        
        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise Exception("Cancellation not supported.")