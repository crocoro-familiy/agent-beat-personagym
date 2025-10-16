# white_agent/agent_executor.py 

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Message
from a2a.utils import new_agent_text_message
from openai import OpenAI
import os
import traceback

class WhiteAgent:
    def __init__(self, persona: str, question: str):
        self.persona = persona
        self.question = question
    def invoke(self) -> str:
        try:
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            system_prompt = (f"You are acting as: {self.persona}. You must answer the following question while staying strictly in character.")
            completion = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": self.question}])
            return completion.choices[0].message.content
        except Exception as e:
            return f"Error calling OpenAI: {str(e)}"

class WhiteAgentExecutor(AgentExecutor):
    """The A2A wrapper for the white agent's logic."""
    
    # The constructor now takes ONLY the persona string.
    def __init__(self, persona: str):
        if not persona or not isinstance(persona, str):
            raise ValueError("A valid persona string must be provided to the executor.")
        
        self.persona = persona
        print(f"White Agent Initialized. Persona: '{self.persona}'")

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        try:
            incoming_message: Message = context.message
            question_text = None
            if incoming_message.parts:
                part_as_dict = incoming_message.parts[0].model_dump()
                if part_as_dict.get('kind') == 'text':
                    question_text = part_as_dict.get('text')
            if not question_text:
                error_msg = "Error: No question was provided in the request from the green agent."
                print(f"White Agent: {error_msg}")
                await event_queue.enqueue_event(new_agent_text_message(error_msg))
                return
            print(f"White Agent: Received question: '{question_text}'")
            agent = WhiteAgent(persona=self.persona, question=question_text)
            result = agent.invoke()
            await event_queue.enqueue_event(new_agent_text_message(result))
        except Exception as e:
            tb_str = traceback.format_exc()
            error_message = f"WHITE AGENT CRASHED:\\n{e}\\n\\nTRACEBACK:\\n{tb_str}"
            await event_queue.enqueue_event(new_agent_text_message(error_message))
        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise Exception("Cancellation not supported.")