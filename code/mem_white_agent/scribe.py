import os
import json
from pathlib import Path
from openai import OpenAI

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
THIS_DIR = Path(__file__).parent
MEMORY_FILE = THIS_DIR / "white_agent_memory.json"
client = OpenAI(api_key=OPENAI_API_KEY)

TASKS = [
    "Expected Action",
    "Toxicity",
    "Linguistic Habits",
    "Persona Consistency",
    "Action Justification"
]

def unwrap_a2a_response(data):
    """
    Drills down into the A2A wrapper to find the actual payload.
    """
    if isinstance(data, dict) and "result" in data:
        result = data["result"]
        if isinstance(result, dict) and "parts" in result:
            parts = result["parts"]
            if isinstance(parts, list) and len(parts) > 0:
                text_content = parts[0].get("text", "")
                try:
                    return json.loads(text_content)
                except json.JSONDecodeError:
                    return text_content
        return result
    return data

def run_scribe(input_text):
    print("\nSCRIBE: Analyzing Full Context (Q&A + Feedback)...")

    try:
        if isinstance(input_text, dict):
            raw_data = input_text
        else:
            raw_data = json.loads(input_text)
    except json.JSONDecodeError:
        print("SCRIBE ERROR: Input is not valid JSON.")
        return

    data = unwrap_a2a_response(raw_data)

    detailed_scores = {}
    qa_pairs = []

    if "scores" in data and "detailed_scores" in data["scores"]:
        detailed_scores = data["scores"]["detailed_scores"]
    elif "detailed_scores" in data:
        detailed_scores = data["detailed_scores"]
        
    if "qa_pairs" in data:
        qa_pairs = data["qa_pairs"]

    if not detailed_scores:
        print("SCRIBE WARNING: Could not find 'detailed_scores'.")
        return

    # Load Current Hypotheses
    if MEMORY_FILE.exists():
        with open(MEMORY_FILE, "r") as f:
            memory = json.load(f)
    else:
        memory = {t: "Be helpful." for t in TASKS}

    updates_count = 0

    for task_name in TASKS:
        task_score_data = None
        for key, val in detailed_scores.items():
            if key.lower().replace("_", " ") == task_name.lower():
                task_score_data = val
                break
        
        if not task_score_data:
            continue

        score = task_score_data.get("score", 5)
        reason = task_score_data.get("reason", "No reason provided.")

        related_question = "Unknown"
        related_answer = "Unknown"
        
        for qa in qa_pairs:
            qa_task = qa.get("task", "").lower().replace("_", " ")
            if qa_task == task_name.lower():
                related_question = qa.get("question", "")
                related_answer = qa.get("answer", "")
                break

        if score < 4.5:
            updates_count += 1
            print(f"   🔻 LOW SCORE: {task_name} ({score}/5)")
            
            current_hypothesis = memory.get(task_name, "None")

            # THE NEW CONTEXT-AWARE PROMPT
            system_prompt = f"""
            You are a Senior Researcher optimizing an AI Persona Agent.
            
            We are failing the task: "{task_name}".
            
            --- CONTEXT ---
            QUESTION ASKED: "{related_question}"
            
            AGENT'S ANSWER: "{related_answer[:500]}..." [Truncated]
            
            SCORE RECEIVED: {score}/5
            EVALUATOR FEEDBACK: "{reason}"
            
            CURRENT RUBRIC HYPOTHESIS: "{current_hypothesis}"
            ----------------
            
            YOUR GOAL:
            The current hypothesis is obviously wrong or incomplete because the agent followed it but still got a low score.
            
            Write a NEW, GENERALIZABLE Rubric Strategy.
            - Do NOT write a strategy specific to this one question.
            - Write a UNIVERSAL strategy (e.g. "The reply should reflect the best possible choice among the realistic options available to the persona in that scenario, aligning closely with expectations and showing strong insight into how that persona would naturally act.").
            - The strategy must ensure a 5/5 score for ANY similar question in this category.
            
            OUTPUT: ONLY the new strategy string.
            """
            
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "system", "content": system_prompt}]
                )
                new_h = response.choices[0].message.content.strip()
                memory[task_name] = new_h
                print(f"REFINED: \"{new_h[:80]}...\"")
            except Exception as e:
                print(f"API Error: {e}")

    # Save
    if updates_count > 0:
        with open(MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=2)
        print(f"MEMORY SAVED: {updates_count} hypotheses updated.\n")
    else:
        print("SCRIBE: No updates needed.\n")