
import logging
import asyncio
import multiprocessing
import json
from uuid import uuid4
import time
import signal
import sys
import os

try:
    from green_agent import start_green_agent
    from white_agent import start_white_agent
    from my_a2a import wait_agent_ready, send_message
except ImportError as e:
    print(f"FATAL LAUNCHER ERROR: Could not import required modules: {e}")
    print("This error is from 'launcher.py'. Ensure main.py is adding 'code/' and 'code/my_util/' to sys.path.")
    sys.exit(1)

GREEN_HOST, GREEN_PORT = "0.0.0.0", 9999
WHITE_HOST, WHITE_PORT = "0.0.0.0", 8001
GREEN_URL = f'http://localhost:{GREEN_PORT}'
WHITE_URL = f'http://localhost:{WHITE_PORT}'


async def launch_evaluation():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    p_green = None
    p_white = None

    def cleanup_processes(signum, frame):
        logger.info("Termination signal received. Cleaning up agent processes...")
        if p_white and p_white.is_alive():
            p_white.terminate()
            p_white.join(timeout=5)
        if p_green and p_green.is_alive():
            p_green.terminate()
            p_green.join(timeout=5)
        logger.info("Cleanup complete.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup_processes)
    signal.signal(signal.SIGTERM, cleanup_processes)

    try:
        multiprocessing.set_start_method('spawn', force=True)
        logger.info("Launching Green Agent process...")
        p_green = multiprocessing.Process(
            target=start_green_agent, args=(GREEN_HOST, GREEN_PORT), daemon=True
        )
        p_green.start()
        logger.info("Launching White Agent process...")
        p_white = multiprocessing.Process(
            target=start_white_agent, args=(WHITE_HOST, WHITE_PORT), daemon=True
        )
        p_white.start()

        logger.info(f"Waiting for Green Agent at {GREEN_URL}...")
        if not await wait_agent_ready(GREEN_URL, timeout=30):
            raise RuntimeError("Green Agent failed to start.")
        logger.info("Green Agent is ready.")
        logger.info(f"Waiting for White Agent at {WHITE_URL}...")
        if not await wait_agent_ready(WHITE_URL, timeout=30):
            raise RuntimeError("White Agent failed to start.")
        logger.info("White Agent is ready.")

        initial_message_text = f"""
        Start PersonaGym evaluation for the agent located at:
        <white_agent_url>
        {WHITE_URL}
        </white_agent_url>
        """
        logger.info("Formatted initial message with tags.")

        logger.info(f"Sending evaluation task to Green Agent... This may take up to 15 minutes.")
        
        send_response = await send_message(
            url=GREEN_URL,
            message=initial_message_text,
            task_id=None,
            timeout=900.0 
        )
        
        logger.info("Task successfully completed.")

        print("\n--- FINAL RESPONSE FROM GREEN AGENT ---")
        try:
            if hasattr(send_response, 'error') and send_response.error:
                print(f"ERROR received from agent: {send_response.error.message}")
            elif hasattr(send_response, 'result') and send_response.result and send_response.result.parts:
                final_text = send_response.result.parts[0].text
                print(final_text)
            else:
                print("Received unknown response:")
                print(send_response.model_dump_json(indent=2))
                
        except Exception as e:
            print(f"Could not parse the final response. Error: {e}")
            print(send_response.model_dump_json(indent=2))
            
    except Exception as e:
        logger.error(f"An error occurred during launch: {e}", exc_info=True)

    finally:
        print("--- END OF RESPONSE ---\n")
        logger.info("Evaluation attempt finished. Terminating agent processes...")
        if p_white and p_white.is_alive():
            logger.info("Terminating White Agent...")
            p_white.terminate()
            p_white.join(timeout=5)
            if p_white.is_alive(): p_white.kill()
        if p_green and p_green.is_alive():
            logger.info("Terminating Green Agent...")
            p_green.terminate()
            p_green.join(timeout=5)
            if p_green.is_alive(): p_green.kill()
        
        logger.info("Launcher script finished.")

if __name__ == "__main__":
    multiprocessing.set_start_method('spawn', force=True)
    asyncio.run(launch_evaluation())