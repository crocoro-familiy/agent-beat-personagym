"""CLI entry point for the PersonaGym Agent Beat integration."""

import typer
import asyncio
import sys
from pathlib import Path
    
app = typer.Typer(help="PersonaGym Agent Beat - Run and evaluate persona agents.")

@app.command()
def green():
    from green_agent import start_green_agent
    """Start the Green Agent (PersonaGym evaluator) server."""
    print("Starting Green Agent...")
    start_green_agent()

@app.command()
def greenstatic():
    from green_agent_static import start_green_agent_static
    """Start the Green Agent (PersonaGym evaluator) server."""
    print("Starting Green Agent (Static)...")
    start_green_agent_static()

@app.command()
def white():
    from white_agent import start_white_agent
    """Start the White Agent (Persona Actor) server."""
    print("Starting White Agent...")
    start_white_agent()


@app.command()
def launch(static: bool = False):
    from launcher import launch_evaluation
    mode_message = "STATIC BENCHMARK" if static else "DYNAMIC EVALUATION"
    print(f"Launching PersonaGym evaluation [{mode_message}]...")
    
    # Pass the static flag to the async launcher
    asyncio.run(launch_evaluation(static_mode=static))


if __name__ == "__main__":
    app()