# agent_executor.py

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Message
from a2a.utils import new_agent_text_message
from run import main
import asyncio
import traceback

class PersonaGymAgent:
    """Agent to handle PersonaGym evaluations."""
    def __init__(self, persona: str):
        self.persona = persona

    async def invoke(self):
        """Executes the PersonaGym evaluation in a separate thread."""
        loop = asyncio.get_event_loop()
        scores = await loop.run_in_executor(None, lambda: main(self.persona, "gpt-4o-mini"))
        return str(scores)

class PersonaGymAgentExecutor(AgentExecutor):
    """Executor for PersonaGym Green Agent."""

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        try:
            incoming_message: Message = context.message
            
            persona_text = "a default persona" 

            if incoming_message.parts:

                first_part = incoming_message.parts[0]
                
                part_as_dict = first_part.model_dump()

                if part_as_dict.get('kind') == 'text':
                    persona_text = part_as_dict.get('text', persona_text)

            agent = PersonaGymAgent(persona=persona_text)
            result = await agent.invoke()
            
            await event_queue.enqueue_event(new_agent_text_message(result))

        except Exception as e:
            tb_str = traceback.format_exc()
            error_message = f"SERVER CRASHED:\n{e}\n\nTRACEBACK:\n{tb_str}"
            print(error_message)
            await event_queue.enqueue_event(new_agent_text_message(error_message))
        
        finally:
            await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.close()