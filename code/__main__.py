# __main__.py

import uvicorn
import toml  # You may need to run: pip install toml

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard

from agent_executor import PersonaGymAgentExecutor 

def load_agent_card_toml() -> dict:
    """Loads agent configuration from agent_card.toml."""
    with open("agent_card.toml", "r") as f:
        return toml.load(f)

if __name__ == "__main__":
    HOST = "0.0.0.0"
    PORT = 9999
    
    # Load agent configuration from the TOML file
    agent_card_toml = load_agent_card_toml()
    
    # Set the agent's public URL based on current host and port
    agent_card_toml['url'] = f'http://{HOST}:{PORT}/'

    # Set up the request handler, which uses your agent logic
    request_handler = DefaultRequestHandler(
        agent_executor=PersonaGymAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    
    # Create the server application
    server = A2AStarletteApplication(
        # Unpack the config dictionary to create the AgentCard object
        agent_card=AgentCard(**agent_card_toml),
        http_handler=request_handler,
    )
    
    # Run the server
    uvicorn.run(server.build(), host=HOST, port=PORT)