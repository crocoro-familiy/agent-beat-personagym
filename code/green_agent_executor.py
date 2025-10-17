from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Message
from a2a.utils import new_agent_text_message
import asyncio
import traceback
import httpx
import json
from uuid import uuid4

# Import the core logic from your PersonaGym code
from run import select_settings, gen_questions, score_answers
from eval_tasks import tasks

class GreenAgentOrchestrator:
    def __init__(self, white_agent_url: str):
        self.white_agent_url = white_agent_url
        self.httpx_client = httpx.AsyncClient(timeout=60.0)

    async def run_full_evaluation(self) -> dict:
        print("Green Agent: Starting evaluation...")
        persona = await self._get_persona_from_profile()
        print(f"Green Agent: Discovered persona: '{persona[:50]}...'")

        loop = asyncio.get_event_loop()
        settings = await loop.run_in_executor(None, select_settings, persona)
        questions_dict = await loop.run_in_executor(None, gen_questions, persona, settings, 5)
        print("Green Agent: Generated questions.")

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
        profile_url = f"{self.white_agent_url}/profile"
        response = await self.httpx_client.get(profile_url)
        response.raise_for_status()
        profile_json = response.json()
        return profile_json["persona_description"]

    async def _ask_questions_and_get_answers(self, questions_dict: dict) -> dict:
        task_to_qa = {task: [] for task in tasks}
        white_agent_client = httpx.AsyncClient(base_url=self.white_agent_url, timeout=120.0)

        for task_name, question_list in questions_dict.items():
            for question in question_list:
                # This is the correct, full JSON-RPC 2.0 payload format
                payload = {
                    "jsonrpc": "2.0",
                    "method": "message/send",
                    "id": str(uuid4()),
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"kind": "text", "text": question}],
                            "messageId": str(uuid4())
                        }
                    }
                }
                # The A2A server expects the root path "/" for JSON-RPC requests
                response = await white_agent_client.post('/', json=payload)
                response.raise_for_status() 
                response_json = response.json()
                
                answer = response_json.get('result', {}).get('parts', [{}])[0].get('text', 'Error: No answer found in response')
                task_to_qa[task_name].append((question, answer))
        
        await white_agent_client.aclose()
        return task_to_qa

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
            
            orchestrator = GreenAgentOrchestrator(white_agent_url=white_agent_url)
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