## agent-beat-personagym
Green and White Agent implementation built on top of the a2a framework for PersonaGym, and integrate to the Agent-Beat platform. The Persona Gym codebase is refactored to reflect the suggested format by the AgentBeat developer. 

## Prerequisites

- Python 3.13 or higher (the latest earthshaker requirement)
- Access to a terminal or command prompt.
- Git, for cloning the repository.
- A code editor (e.g., Visual Studio Code) is recommended.

## Python Environment & SDK Installation

We recommend using a virtual environment for Python projects. The A2A Python SDK uses `uv` for dependency management, but you can use `pip` with `venv` as well.

1. **Create and activate a virtual environment:**

    Using `venv` (standard library):

    === "Mac/Linux"

        ```sh
        python -m venv .venv
        source .venv/bin/activate
        ```

    === "Windows"

        ```powershell
        python -m venv .venv
        .venv\Scripts\activate
        ```

2. **Install needed Python dependencies along with the A2A SDK and its dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

## Verify Installation

After installation, you should be able to import the `a2a` package in a Python interpreter:

```bash
python -c "import a2a; print('A2A SDK imported successfully')"
```

If this command runs without error and prints the success message, your environment is set up correctly.

## Run the code (command prompt)

To run and test the Persona Agent in the command prompt, please use the following command:

```bash
python main.py launch
```
Note that we also provide a static benchmark for testing a general LLM model, please use the following to run static benchmark:

```bash
python main.py launch --static
```

Please remember to set up your API keys properly.

## Launch the AgentBeat controller 

Run the following command to start the controller:
```
agentbeats run_ctrl
```
Once it’s running, you should see a local management page similar to the one shown below. From there, you can also access your agent through the proxy URL provided by the controller — for example, try checking whether `.well-known/agent-card.json` can be successfully fetched.

![Green Agent Host screenshot](image/green_agent_host.png "Green Agent Host")
