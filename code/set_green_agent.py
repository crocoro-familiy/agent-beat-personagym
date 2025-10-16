import uvicorn
import toml  

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard

from green_agent_executor import GreenAgentExecutor 

def load_agent_card_toml() -> dict:
    """Loads agent configuration from agent_card.toml."""
    with open("green_agent_card.toml", "r") as f:
        return toml.load(f)

if __name__ == "__main__":
    HOST = "0.0.0.0"
    PORT = 9999
    
    agent_card_toml = load_agent_card_toml()
    
    agent_card_toml['url'] = f'http://{HOST}:{PORT}/'

    request_handler = DefaultRequestHandler(
        agent_executor=GreenAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    
    server = A2AStarletteApplication(
        agent_card=AgentCard(**agent_card_toml),
        http_handler=request_handler,
    )
    
    uvicorn.run(server.build(), host=HOST, port=PORT)