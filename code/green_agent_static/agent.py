import uvicorn
import tomllib
import json
import asyncio
import traceback
import httpx
import sys
import re
import statistics  
from uuid import uuid4
from pathlib import Path
import os
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

from run import load_questions, score_answers, benchmark_personas

from eval_tasks import tasks as default_tasks

RUBRICS_DIR = PROJECT_ROOT / "rubrics"
STATIC_QUESTIONS_DIR_NAME = "benchmark-v1" 

def parse_tags(text: str) -> dict:
    params = {}
    pattern = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(text)
    for tag, value in matches:
        params[tag.lower()] = value.strip()
    return params

class StaticGreenAgentOrchestrator:
    def __init__(self, white_agent_url: str, httpx_client: httpx.AsyncClient):
        self.white_agent_url = white_agent_url
        self.httpx_client = httpx_client

    async def run_benchmark_for_persona(self, persona: str, questions_dict: dict) -> dict:
        """
        Iterates through DEFAULT TASKS found in the questions dictionary, 
        asks the White Agent using JSON-RPC format, and scores the answers.
        """
        
        task_to_qa = {task: [] for task in default_tasks if task in questions_dict}

        for task_name in task_to_qa.keys():
            question_list = questions_dict[task_name]
            
            for question in question_list:
                # print(f"Green Agent: Sending question for task '{task_name}'...")
                answer = "Error: No response from agent."
                
                payload = {
                    "jsonrpc": "2.0",
                    "method": "message/send",
                    "id": str(uuid4()),
                    "params": {
                        "message": {
                            "messageId": str(uuid4()),
                            "role": "user",
                            "parts": [
                                {"kind": "text", "text": persona},  # Part 1: Persona
                                {"kind": "text", "text": question}  # Part 2: Question
                            ]
                        }
                    }
                }

                try:
                    response = await self.httpx_client.post(self.white_agent_url, json=payload, timeout=180.0)
                    response.raise_for_status()
                    response_json = response.json()

                    if 'result' in response_json and 'parts' in response_json['result']:
                        answer = response_json['result']['parts'][0].get('text', 'Error: No text in response part')
                    elif 'error' in response_json:
                        answer = f"Error from agent: {response_json['error'].get('message', 'Unknown Error')}"
                    else:
                        answer = str(response_json)

                except httpx.ReadTimeout:
                    answer = "Error: Timed out waiting for White Agent to respond."
                except Exception as e:
                    answer = f"Error during communication: {e}"

                task_to_qa[task_name].append((question, answer))

        # --- Scoring ---
        full_rubrics_path = str(RUBRICS_DIR / "general")
        loop = asyncio.get_event_loop()
        scores = await loop.run_in_executor(
            None, score_answers, persona, task_to_qa, full_rubrics_path
        )
        return scores

class StaticGreenAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        try:
            incoming_message = context.message
            raw_text = ""
            if incoming_message.parts:
                part_as_dict = incoming_message.parts[0].model_dump()
                if part_as_dict.get('kind') == 'text':
                    raw_text = part_as_dict.get('text', "")
            
            parsed_params = parse_tags(raw_text)
            white_agent_url = parsed_params.get('white_agent_url')

            if not white_agent_url:
                raise ValueError("Could not extract <white_agent_url> from request.")

            print(f"STARTING STATIC BENCHMARK on {white_agent_url}")
            print(f"Total Personas to evaluate: {len(benchmark_personas)}")

            shared_client = httpx.AsyncClient(timeout=180.0)
            
             # Stores just the overall average per persona
            all_persona_scores = {} 
            
            # Stores lists of scores per task (e.g., "Expected Action": [4.5, 3.0, ...])
            task_scores_accumulator = {task: [] for task in default_tasks}

            try:
                for i, persona in enumerate(benchmark_personas):
                    print(f"[{i+1}/{len(benchmark_personas)}] Processing: {persona[:40]}...")
                    
                    try:
                        questions_dict = await asyncio.to_thread(load_questions, persona, STATIC_QUESTIONS_DIR_NAME)
                    except Exception as e:
                        print(f" Failed to load questions: {e}")
                        all_persona_scores[persona] = 0
                        continue

                    orchestrator = StaticGreenAgentOrchestrator(white_agent_url, shared_client)
                    scores = await orchestrator.run_benchmark_for_persona(persona, questions_dict)
                    
                    all_scores_list = []
                    if isinstance(scores, dict):
                        for task_data in scores.values():
                            # Flatten all scores from all tasks into one list
                            if isinstance(task_data, dict) and 'scores' in task_data and isinstance(task_data['scores'], list):
                                all_scores_list.extend(task_data['scores'])
                    
                    numeric_scores = [s for s in all_scores_list if isinstance(s, (int, float))]
                    
                    # Calculate the average for this persona
                    persona_score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else 0
                    
                    all_persona_scores[persona] = persona_score
                    print(f"   ✅ Score: {persona_score:.2f}")

                    # Update Task Accumulators (For the final breakdown)
                    # We assume 'scores' keys correspond to default_tasks
                    for task_name in default_tasks:
                        if task_name in scores and isinstance(scores[task_name], dict):
                            raw_task_scores = scores[task_name].get("scores", [])
                            # Filter numeric only to be safe
                            valid_task_scores = [s for s in raw_task_scores if isinstance(s, (int, float))]
                            
                            if valid_task_scores:
                                avg_task = sum(valid_task_scores) / len(valid_task_scores)
                                task_scores_accumulator[task_name].append(avg_task)

            finally:
                await shared_client.aclose()

            # FINAL REPORT GENERATION
            avg_benchmark_score = sum(all_persona_scores.values()) / len(all_persona_scores) if all_persona_scores else 0
            
            task_stats = {}
            for task_name, values in task_scores_accumulator.items():
                if values:
                    mean_val = statistics.mean(values)
                    stdev_val = statistics.stdev(values) if len(values) > 1 else 0.0
                    task_stats[task_name] = f"{mean_val:.2f} ± {stdev_val:.2f}"
                else:
                    task_stats[task_name] = "N/A"

            final_report = {
                "summary": "Static Benchmark Complete",
                "total_personas": len(benchmark_personas),
                "average_benchmark_score": round(avg_benchmark_score, 2),
                "task_breakdown": task_stats, 
                "persona_details": all_persona_scores
            }
            
            await event_queue.enqueue_event(new_agent_text_message(json.dumps(final_report, indent=2)))

        except Exception as e:
            traceback.print_exc()
            await event_queue.enqueue_event(new_agent_text_message(f"Error: {e}"))
        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        """Required implementation for abstract method."""
        await event_queue.enqueue_event(new_agent_text_message("Cancellation not supported for static benchmark."))
        await event_queue.close()

def load_agent_card_toml(card_filename="green_agent_card.toml") -> dict:
    card_path = AGENT_DIR / card_filename
    if not card_path.exists():
         raise FileNotFoundError(f"Cannot find {card_filename} in {AGENT_DIR}")
    with open(card_path, "rb") as f:
        return tomllib.load(f)

def start_green_agent_static(host="0.0.0.0", port=9999):
    print(f"Starting STATIC Green Agent (Benchmark Mode)...")
    
    try:
        agent_card_toml = load_agent_card_toml()

        dotenv.load_dotenv()

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
        env_agent_url = os.getenv("AGENT_URL")
        
        if env_agent_url:
            print(f"[INFO] Using AGENT_URL from environment: {env_agent_url}")
            agent_card_toml["url"] = env_agent_url
        else:
            # Fallback: internal URL (useful for local manual runs)
            agent_card_toml["url"] = f"http://{final_host}:{final_port}/"
            print("[INFO] Using INTERNAL_URL from AGENTCARD")

        print(f"Agent Card loaded. URL set to {agent_card_toml['url']}")

        request_handler = DefaultRequestHandler(
            agent_executor=StaticGreenAgentExecutor(),
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
    start_green_agent_static()