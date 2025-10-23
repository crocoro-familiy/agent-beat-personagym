import json
import os
from pathlib import Path
from typing import Any
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from run import select_settings, gen_questions, score_answers
from eval_tasks import tasks
from uuid import uuid4


THIS_DIR = Path(__file__).parent
HTML_FILE = THIS_DIR / "persona-eval-redesign-crocoro.html"
A2A_BASE_URL = os.environ.get("A2A_BASE_URL", "http://localhost:9999")
RESULTS_DIR = THIS_DIR / "agent_results"
RESULTS_DIR.mkdir(exist_ok=True)


app = FastAPI()

# Allow browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    if not HTML_FILE.exists():
        raise HTTPException(status_code=404, detail="HTML file not found")
    return HTMLResponse(HTML_FILE.read_text(encoding="utf-8"))


def _save_intermediate_result(session_id: str, step_name: str, data: Any) -> None:
    result_file = RESULTS_DIR / f"{session_id}.json"
    
    if result_file.exists():
        with open(result_file, 'r', encoding='utf-8') as f:
            result_data = json.load(f)
    else:
        result_data = {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "steps": {}
        }
    
    result_data["steps"][step_name] = {
        "timestamp": datetime.now().isoformat(),
        "data": data
    }
    
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)


async def _ask_questions_to_white_agent(questions_dict: dict, white_agent_url: str) -> dict:
    task_to_qa = {task: [] for task in tasks}
    
    async with httpx.AsyncClient(base_url=white_agent_url, timeout=120.0) as client:
        for task_name, question_list in questions_dict.items():
            for question in question_list:
                payload = {
                    "jsonrpc": "2.0",
                    "method": "message/send",
                    "id": str(uuid4()),
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"kind": "text", "text": question}],
                            "messageId": str(uuid4())
                        }
                    }
                }
                
                response = await client.post('/', json=payload)
                response.raise_for_status()
                response_json = response.json()
                
                answer = response_json.get('result', {}).get('parts', [{}])[0].get('text', 'Error: No answer found in response')
                task_to_qa[task_name].append((question, answer))
    
    return task_to_qa


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "a2aBaseUrl": A2A_BASE_URL})


@app.get("/results")
async def list_results() -> JSONResponse:
    result_files = []
    for file_path in RESULTS_DIR.glob("*.json"):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                result_files.append({
                    "session_id": data.get("session_id"),
                    "created_at": data.get("created_at"),
                    "steps": list(data.get("steps", {}).keys()),
                    "file_name": file_path.name
                })
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
    
    result_files.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return JSONResponse({"results": result_files})


@app.get("/results/{session_id}")
async def get_result(session_id: str) -> JSONResponse:
    """Get specific session result"""
    result_file = RESULTS_DIR / f"{session_id}.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    
    try:
        with open(result_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return JSONResponse(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading result: {str(e)}")


@app.post("/connect_white_agent")
async def connect_white_agent(request: Request) -> JSONResponse:
    """Connect to White Agent and get persona description"""
    body = await request.json()
    white_agent_url: str = (body or {}).get("white_agent_url") or ""
    if not isinstance(white_agent_url, str) or not white_agent_url.strip():
        raise HTTPException(status_code=400, detail="white_agent_url is required")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get White Agent's persona description
            profile_url = f"{white_agent_url.rstrip('/')}/profile"
            response = await client.get(profile_url)
            response.raise_for_status()
            profile_data = response.json()
            persona_description = profile_data.get("persona_description", "")
            
            if not persona_description:
                raise HTTPException(status_code=400, detail="No persona_description found in White Agent")
            
            return JSONResponse({
                "success": True,
                "persona_description": persona_description,
                "white_agent_url": white_agent_url
            })
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Timeout connecting to White Agent")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot connect to White Agent. Is it running?")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error connecting to White Agent: {str(e)}")


@app.post("/evaluate_with_white_agent_stream")
async def evaluate_with_white_agent_stream(request: Request) -> StreamingResponse:
    """Use White Agent for streaming evaluation, display progress in real-time"""
    body = await request.json()
    white_agent_url: str = (body or {}).get("white_agent_url") or ""
    if not isinstance(white_agent_url, str) or not white_agent_url.strip():
        raise HTTPException(status_code=400, detail="white_agent_url is required")
    
    # Generate session ID
    session_id = str(uuid4())
    
    async def _async_iter():
        import json as _json
        
        try:
            yield (_json.dumps({"type": "status", "message": "Connecting to White Agent..."}) + "\n").encode()
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                profile_url = f"{white_agent_url.rstrip('/')}/profile"
                response = await client.get(profile_url)
                response.raise_for_status()
                profile_data = response.json()
                persona_description = profile_data.get("persona_description", "")
                
                if not persona_description:
                    raise Exception("No persona_description found in White Agent")
                
                # Save connection information
                _save_intermediate_result(session_id, "connection", {
                    "white_agent_url": white_agent_url,
                    "persona_description": persona_description
                })
                
                yield (_json.dumps({"type": "status", "message": f"Connected! Persona: {persona_description[:100]}..."}) + "\n").encode()
            
        
            yield (_json.dumps({"type": "status", "message": "Connecting to Green Agent..."}) + "\n").encode()
            
            
            yield (_json.dumps({"type": "status", "message": "Sending evaluation request..."}) + "\n").encode()
            
            yield (_json.dumps({"type": "status", "message": "Green Agent is evaluating White Agent..."}) + "\n").encode()
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                profile_url = f"{white_agent_url.rstrip('/')}/profile"
                response = await client.get(profile_url)
                response.raise_for_status()
                profile_data = response.json()
                persona_description = profile_data.get("persona_description", "")
                
                if not persona_description:
                    raise Exception("No persona_description found in White Agent")
            
            yield (_json.dumps({"type": "status", "message": "Step 1: Generating evaluation settings..."}) + "\n").encode()
            settings = select_settings(persona_description)
            
            # Save settings
            _save_intermediate_result(session_id, "settings", settings)
            
            yield (_json.dumps({"type": "settings", "data": settings}) + "\n").encode()
            
            yield (_json.dumps({"type": "status", "message": "Step 2: Generating evaluation questions..."}) + "\n").encode()
            questions = gen_questions(persona_description, settings)
            
            # Save questions
            _save_intermediate_result(session_id, "questions", questions)
            
            yield (_json.dumps({"type": "questions", "data": questions}) + "\n").encode()
            
            yield (_json.dumps({"type": "status", "message": "Step 3: Asking questions to White Agent..."}) + "\n").encode()
           
            task_to_qa = await _ask_questions_to_white_agent(questions, white_agent_url)
            
            # Save QA pairs
            _save_intermediate_result(session_id, "qa_pairs", task_to_qa)
            
            for task, qa_list in task_to_qa.items():
                for q, a in qa_list:
                    yield (_json.dumps({"type": "qa", "task": task, "question": q, "answer": a}) + "\n").encode()
            yield (_json.dumps({"type": "qa_done"}) + "\n").encode()
            
            yield (_json.dumps({"type": "status", "message": "Step 4: Scoring responses..."}) + "\n").encode()
            result = score_answers(persona_description, task_to_qa, return_explanations=True)
            
            # Calculate overall score
            overall_scores = []
            for task in result:
                if result[task]["scores"]:
                    overall_scores.append(sum(result[task]["scores"]) / len(result[task]["scores"]))
            
            if overall_scores:
                overall = sum(overall_scores) / len(overall_scores)
                result["PersonaScore"] = {"scores": [overall]}

            # Save detailed scores
            _save_intermediate_result(session_id, "scores", {
                "scores": result,
                "personaScore": result["PersonaScore"]
            })
            
            # Convert to format with scores and reasons for frontend display
            display_scores = {}
            for task, data in result.items():
                if task != "PersonaScore" and data["scores"]:
                    # Take only the first reason
                    first_reason = data["reasons"][0] if data["reasons"] and len(data["reasons"]) > 0 else "No explanation provided"
                    display_scores[task] = {
                        "score": data["scores"][0],
                        "reason": first_reason
                    }
                    print(f"DEBUG: {task} - Score: {data['scores'][0]}, Reason length: {len(first_reason)}, Reason preview: {first_reason[:100]}...")
            
            persona_score = result["PersonaScore"]["scores"][0] if result["PersonaScore"]["scores"] else 0
            
            print(f"DEBUG: Sending scores to frontend: {display_scores}")
            if display_scores:
                first_task = list(display_scores.keys())[0]
                first_data = display_scores[first_task]
                print(f"DEBUG: First task '{first_task}' data: {first_data}")
                print(f"DEBUG: First task reason length: {len(first_data.get('reason', ''))}")
                print(f"DEBUG: First task reason preview: {first_data.get('reason', '')[:200]}...")
            yield (_json.dumps({"type": "scores", "scores": display_scores, "personaScore": persona_score}) + "\n").encode()
            yield (_json.dumps({"type": "status", "message": "Evaluation complete!"}) + "\n").encode()
            yield (_json.dumps({"type": "done", "white_agent_url": white_agent_url, "session_id": session_id}) + "\n").encode()
            
        except Exception as e:
            print(f"DEBUG: Exception in stream: {e}")
            yield (_json.dumps({"type": "error", "error": str(e)}) + "\n").encode()
    
    return StreamingResponse(_async_iter(), media_type="application/x-ndjson")




def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()


