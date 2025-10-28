"""CLI entry point for the PersonaGym Agent Beat integration."""

import typer
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
CODE_DIR = PROJECT_ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

try:
    from green_agent import start_green_agent
    from white_agent import start_white_agent
    from launcher import launch_evaluation
except ImportError as e:
    print(f"ERROR: Could not import agent/launcher start functions: {e}")
    print("Please ensure:")
    print("1. Agents and launcher are refactored into agent.py/launcher.py with __init__.py.")
    print("2. This main.py script is in the project root directory.")
    print("3. Necessary __init__.py files exist in 'code', 'code/green_agent', 'code/white_agent'.")
    sys.exit(1)


app = typer.Typer(help="PersonaGym Agent Beat - Run and evaluate persona agents.")


@app.command()
def green():
    """Start the Green Agent (PersonaGym evaluator) server."""
    print("Starting Green Agent...")
    start_green_agent() 


@app.command()
def white():
    """Start the White Agent (Persona Actor) server."""
    print("Starting White Agent...")
    start_white_agent()


@app.command()
def launch():
    """Launch the complete PersonaGym evaluation workflow (starts both agents)."""
    print("Launching PersonaGym evaluation...")
    asyncio.run(launch_evaluation())


if __name__ == "__main__":
    app()