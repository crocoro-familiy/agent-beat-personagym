"""CLI entry point for the PersonaGym Agent Beat integration."""

import typer
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
CODE_DIR = PROJECT_ROOT / "code"
MY_UTIL_DIR = CODE_DIR / "my_util" # Your my_a2a.py is in here

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
if str(MY_UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(MY_UTIL_DIR))


try:
    from green_agent import start_green_agent
    
    from white_agent import start_white_agent
    
    from launcher import launch_evaluation
    
except ImportError as e:
    print(f"ERROR: Could not import agent/launcher start functions: {e}")
    print("Please ensure:")
    print("1. This main.py script is in the project root directory.")
    print(f"2. Necessary __init__.py files exist in:\n  - {CODE_DIR}\n  - {CODE_DIR / 'green_agent'}\n  - {CODE_DIR / 'white_agent'}\n  - {MY_UTIL_DIR}")
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