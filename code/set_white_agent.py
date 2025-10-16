import uvicorn
import toml
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from white_agent_executor import WhiteAgentExecutor
from starlette.responses import JSONResponse
from starlette.routing import Route

def load_agent_card_toml() -> dict:
    with open("white_agent_card.toml", "r") as f:
        return toml.load(f)

async def get_profile(request):
    """Reads the local TOML file and returns the metadata section."""
    config = load_agent_card_toml()
    metadata = config.get("metadata", {})
    return JSONResponse(metadata)

if __name__ == "__main__":
    HOST = "0.0.0.0"
    PORT = 8001
    agent_card_toml = load_agent_card_toml()
    
    try:
        persona = agent_card_toml["metadata"]["persona_description"]
    except KeyError:
        raise ValueError("CRITICAL: white_agent_card.toml MUST have [metadata] with 'persona_description' key.")

    agent_card_for_public = AgentCard(**agent_card_toml)
    agent_card_for_public.url = f'http://{HOST}:{PORT}/'
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
    app.routes.append(Route("/profile", endpoint=get_profile))
    
    print(f"--- Starting White Agent on port {PORT} ---")
    print(f"--- Custom profile available at http://{HOST}:{PORT}/profile ---")
    uvicorn.run(app, host=HOST, port=PORT)