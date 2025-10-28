import logging
import asyncio
import multiprocessing
import json # Keep json import for potential future needs
from uuid import uuid4
import time # Import time for potential delays if needed
import signal # Import signal for better process termination handling
import sys # Import sys to exit

# Import the utility functions from my_a2a.py
# Make sure my_a2a.py is in the same directory or accessible via PYTHONPATH
try:
    from my_a2a import wait_agent_ready, send_message, stream_events
except ImportError:
    print("ERROR: Could not import functions from my_a2a.py.")
    print("Please ensure my_a2a.py is in the same directory as kick_off.py or in your PYTHONPATH.")
    sys.exit(1)

# Import the start functions from your refactored agent modules
# Adjust the import paths based on your final directory structure
# Assuming kick_off.py is in the project root, and agents are in code/green_agent and code/white_agent
try:

    from code.green_agent import start_green_agent
    from code.white_agent import start_white_agent
except ImportError as e:
    print(f"ERROR: Could not import agent start functions: {e}")
    print("Please ensure:")
    print("1. Your agents have been refactored into agent.py/__init__.py structure.")
    print("2. The import paths above correctly point to your agent modules.")
    print("3. Necessary __init__.py files exist.")
    print("4. If running from the project root, ensure 'code' is in your PYTHONPATH or use relative imports as shown.")
    sys.exit(1)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    green_host, green_port = "0.0.0.0", 9999
    white_host, white_port = "0.0.0.0", 8001 
    green_agent_url = f'http://localhost:{green_port}' 
    white_agent_url_to_test = f'http://localhost:{white_port}'

    p_green = None
    p_white = None

    def cleanup_processes(signum, frame):
        """Signal handler to terminate agent processes."""
        logger.info("Termination signal received. Cleaning up agent processes...")
        if p_white and p_white.is_alive():
            logger.info("Terminating White Agent...")
            p_white.terminate()
            p_white.join(timeout=5)
        if p_green and p_green.is_alive():
            logger.info("Terminating Green Agent...")
            p_green.terminate()
            p_green.join(timeout=5)
        logger.info("Cleanup complete.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup_processes) 
    signal.signal(signal.SIGTERM, cleanup_processes) 

    try:
        logger.info("Launching Green Agent process...")
        p_green = multiprocessing.Process(
            target=start_green_agent, args=(green_host, green_port), daemon=True
        )
        p_green.start()

        logger.info("Launching White Agent process...")
        p_white = multiprocessing.Process(
            target=start_white_agent, args=(white_host, white_port), daemon=True
        )
        p_white.start()

        logger.info(f"Waiting for Green Agent at {green_agent_url}...")
        if not await wait_agent_ready(green_agent_url, timeout=30): # Increased timeout
            logger.error(f"CRITICAL ERROR: Green Agent did not become ready at {green_agent_url}.")
            raise RuntimeError("Green Agent failed to start.")
        logger.info("Green Agent is ready.")

        logger.info(f"Waiting for White Agent at {white_agent_url_to_test}...")
        if not await wait_agent_ready(white_agent_url_to_test, timeout=30): # Increased timeout
            logger.error(f"CRITICAL ERROR: White Agent did not become ready at {white_agent_url_to_test}.")
            raise RuntimeError("White Agent failed to start.")
        logger.info("White Agent is ready.")

        task_id = str(uuid4())
        initial_message_text = f"""
            Start PersonaGym evaluation for the agent located at:
            <white_agent_url>
            {white_agent_url_to_test}
            </white_agent_url>
        """
        logger.info("Formatted initial message with tags.")

        logger.info(f"Sending evaluation task (ID: {task_id}) to Green Agent...")
        send_response = await send_message(
            url=green_agent_url,
            message=initial_message_text,
            task_id=task_id
        )
        logger.info("Task successfully sent to Green Agent.")

        logger.info("Streaming results from Green Agent...")
        print("\n--- RESPONSE STREAM FROM GREEN AGENT ---")
        async for event in stream_events(url=green_agent_url, task_id=task_id):
            if event.kind == 'message' and event.message and event.message.parts:
                for part in event.message.parts:
                    if part.kind == 'text':
                        print(part.text) 
            elif event.kind == 'task_status':
                 print(f"[Status Update: Task {event.task_id} is {event.status}]")
                 if event.status in ('completed', 'failed', 'cancelled'):
                      break 
           

    except Exception as e:
        logger.error(f"An error occurred during kickoff: {e}", exc_info=True)

    finally:
        print("--- END OF RESPONSE STREAM ---\n")
        logger.info("Evaluation attempt finished. Terminating agent processes...")
        if p_white and p_white.is_alive():
            logger.info("Terminating White Agent...")
            p_white.terminate() 
            p_white.join(timeout=5) 
            if p_white.is_alive():
                logger.warning("White Agent did not terminate gracefully. Forcing kill.")
                p_white.kill() 
        if p_green and p_green.is_alive():
            logger.info("Terminating Green Agent...")
            p_green.terminate()
            p_green.join(timeout=5)
            if p_green.is_alive():
                logger.warning("Green Agent did not terminate gracefully. Forcing kill.")
                p_green.kill()
        logger.info("Kickoff script finished.")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    multiprocessing.set_start_method('spawn', force=True) # Use 'spawn' for more safety

    asyncio.run(main())