import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest
from run import select_settings, gen_questions, gen_answers, score_answers
from eval_tasks import tasks
from uuid import uuid4


THIS_DIR = Path(__file__).parent
HTML_FILE = THIS_DIR / "persona-eval-redesign-crocoro.html"
A2A_BASE_URL = os.environ.get("A2A_BASE_URL", "http://localhost:9999")


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


def _extract_scores_from_response(payload: dict[str, Any]) -> dict[str, float]:
    """Extract rubric scores regardless of envelope shape (result/message/top-level)."""
    import ast
    import re
    import json

    scores: dict[str, float] = {}
    try:
        print(f"DEBUG: _extract_scores_from_response input: {payload}")
        
        # Collect texts from any parts list we can find
        nodes: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            nodes.append(payload)
            res = payload.get("result")
            if isinstance(res, dict):
                nodes.append(res)
            msg = payload.get("message")
            if isinstance(msg, dict):
                nodes.append(msg)

        texts: list[str] = []
        for n in nodes:
            parts = n.get("parts")
            if isinstance(parts, list):
                for p in parts:
                    if isinstance(p, dict):
                        t = p.get("text") or p.get("content")
                        if isinstance(t, str):
                            texts.append(t)

        combined = "\n".join(texts)
        print(f"DEBUG: Combined text: {combined}")
        
        if not combined:
            return scores

        try:
            json_match = re.search(r'\{[^{}]*"persona_score"[^{}]*\}', combined, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                print(f"DEBUG: Found JSON match: {json_str}")
                obj = json.loads(json_str)
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        try:
                            scores[str(k)] = float(v)
                        except Exception:
                            continue
                    print(f"DEBUG: Extracted from JSON: {scores}")
                    return scores
        except Exception as e:
            print(f"DEBUG: JSON parsing failed: {e}")

        try:
            scores_match = re.search(r'Final Scores:\s*(\{.*\})', combined, re.DOTALL)
            if scores_match:
                scores_str = scores_match.group(1)
                print(f"DEBUG: Found scores text: {scores_str}")
                
                # 尝试解析JSON
                try:
                    obj = json.loads(scores_str)
                    if isinstance(obj, dict):
                        # 处理嵌套的per_task_scores
                        if "per_task_scores" in obj and isinstance(obj["per_task_scores"], dict):
                            for k, v in obj["per_task_scores"].items():
                                try:
                                    scores[str(k)] = float(v)
                                except Exception:
                                    continue
                        if "persona_score" in obj:
                            scores["persona_score"] = float(obj["persona_score"])
                        
                        print(f"DEBUG: Extracted from text: {scores}")
                        return scores
                except json.JSONDecodeError as je:
                    print(f"DEBUG: JSON decode error: {je}")
                    for key in ["persona_score", "Expected Action", "Toxicity", "Linguistic Habits", "Persona Consistency", "Action Justification"]:
                        pattern = f'"{key}"\\s*:\\s*([0-9.]+)'
                        match = re.search(pattern, scores_str)
                        if match:
                            scores[key] = float(match.group(1))
                    
                    if scores:
                        print(f"DEBUG: Extracted from regex: {scores}")
                        return scores
        except Exception as e:
            print(f"DEBUG: Text parsing failed: {e}")

        for m in re.finditer(r"'([^']+)'\s*:\s*([0-9]+(?:\.[0-9]+)?)", combined):
            scores[m.group(1)] = float(m.group(2))
            
        print(f"DEBUG: Final extracted scores: {scores}")
    except Exception as e:
        print(f"DEBUG: Exception in _extract_scores_from_response: {e}")
        return {}
    return scores


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


@app.post("/run")
@app.post("/run/")
async def run(request: Request) -> JSONResponse:
    body = await request.json()
    persona: str = (body or {}).get("persona") or ""
    if not isinstance(persona, str) or not persona.strip():
        raise HTTPException(status_code=400, detail="persona is required")

    # First try: use A2A SDK (same as test_client.py)
    try:
        async with httpx.AsyncClient(timeout=None) as httpx_client:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=A2A_BASE_URL)
            agent_card = await resolver.get_agent_card()
            client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)
            params = MessageSendParams(
                **{
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": persona.strip()}],
                        "messageId": os.urandom(8).hex(),
                    }
                }
            )
            req = SendMessageRequest(id=os.urandom(8).hex(), params=params)
            resp_obj = await client.send_message(req)
            data = resp_obj.model_dump(mode="json", exclude_none=True)
    except Exception:
        # Fallback HTTP modes if SDK fails
        payload = {
            "id": os.urandom(8).hex(),
            "params": {
                "message": {
                    "role": "user",
                    "parts": [
                        {"kind": "text", "text": persona.strip()},
                    ],
                    "messageId": os.urandom(8).hex(),
                }
            },
        }
        url = f"{A2A_BASE_URL.rstrip('/')}/v1/messages:send"
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    rpc_url = f"{A2A_BASE_URL.rstrip('/')}/"
                    rpc_body = {
                        "id": os.urandom(8).hex(),
                        "jsonrpc": "2.0",
                        "method": "messages.send",
                        "params": payload["params"],
                    }
                    resp = await client.post(rpc_url, json=rpc_body)
                if resp.status_code >= 400:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                data = resp.json()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Upstream error: {e}") from e

    scores = _extract_scores_from_response(data)
    persona_score = scores.get("PersonaScore")
    return JSONResponse({"scores": scores, "personaScore": persona_score})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "a2aBaseUrl": A2A_BASE_URL})


@app.post("/connect_white_agent")
async def connect_white_agent(request: Request) -> JSONResponse:
    """连接到White Agent并获取persona description"""
    body = await request.json()
    white_agent_url: str = (body or {}).get("white_agent_url") or ""
    if not isinstance(white_agent_url, str) or not white_agent_url.strip():
        raise HTTPException(status_code=400, detail="white_agent_url is required")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 获取White Agent的persona description
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


@app.post("/evaluate_with_white_agent")
async def evaluate_with_white_agent(request: Request) -> JSONResponse:
    """使用White Agent进行完整评估（类似kick_off.py的功能）"""
    body = await request.json()
    white_agent_url: str = (body or {}).get("white_agent_url") or ""
    if not isinstance(white_agent_url, str) or not white_agent_url.strip():
        raise HTTPException(status_code=400, detail="white_agent_url is required")
    
    try:
        async with httpx.AsyncClient(timeout=None) as httpx_client:
            # 1. 获取Green Agent的卡片
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=A2A_BASE_URL)
            green_agent_card = await resolver.get_agent_card()
            client = A2AClient(httpx_client=httpx_client, agent_card=green_agent_card)
            
            # 2. 发送White Agent URL给Green Agent进行评估
            params = MessageSendParams(
                **{
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": white_agent_url.strip()}],
                        "messageId": str(uuid4()),
                    }
                }
            )
            req = SendMessageRequest(id=str(uuid4()), params=params)
            resp_obj = await client.send_message(req)
            data = resp_obj.model_dump(mode="json", exclude_none=True)
            
            # 3. 提取评分结果
            scores = _extract_scores_from_response(data)
            persona_score = scores.get("PersonaScore")
            
            return JSONResponse({
                "success": True,
                "scores": scores,
                "personaScore": persona_score,
                "white_agent_url": white_agent_url
            })
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


@app.post("/evaluate_with_white_agent_stream")
async def evaluate_with_white_agent_stream(request: Request) -> StreamingResponse:
    """使用White Agent进行流式评估，实时显示进度"""
    body = await request.json()
    white_agent_url: str = (body or {}).get("white_agent_url") or ""
    if not isinstance(white_agent_url, str) or not white_agent_url.strip():
        raise HTTPException(status_code=400, detail="white_agent_url is required")
    
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
            yield (_json.dumps({"type": "settings", "data": settings}) + "\n").encode()
            
            yield (_json.dumps({"type": "status", "message": "Step 2: Generating evaluation questions..."}) + "\n").encode()
            questions = gen_questions(persona_description, settings)
            yield (_json.dumps({"type": "questions", "data": questions}) + "\n").encode()
            
            yield (_json.dumps({"type": "status", "message": "Step 3: Asking questions to White Agent..."}) + "\n").encode()
           
            task_to_qa = await _ask_questions_to_white_agent(questions, white_agent_url)
            
            for task, qa_list in task_to_qa.items():
                for q, a in qa_list:
                    yield (_json.dumps({"type": "qa", "task": task, "question": q, "answer": a}) + "\n").encode()
            yield (_json.dumps({"type": "qa_done"}) + "\n").encode()
            
            yield (_json.dumps({"type": "status", "message": "Step 4: Scoring responses..."}) + "\n").encode()
            scores = score_answers(persona_description, task_to_qa)
            persona_score = sum(scores.values()) / max(len(scores.keys()), 1)
            
            yield (_json.dumps({"type": "scores", "scores": scores, "personaScore": persona_score}) + "\n").encode()
            yield (_json.dumps({"type": "status", "message": "Evaluation complete!"}) + "\n").encode()
            yield (_json.dumps({"type": "done", "white_agent_url": white_agent_url}) + "\n").encode()
            
        except Exception as e:
            print(f"DEBUG: Exception in stream: {e}")
            yield (_json.dumps({"type": "error", "error": str(e)}) + "\n").encode()
    
    return StreamingResponse(_async_iter(), media_type="application/x-ndjson")


@app.post("/run_stream")
async def run_stream(request: Request) -> StreamingResponse:
    body = await request.json()
    persona: str = (body or {}).get("persona") or ""
    if not isinstance(persona, str) or not persona.strip():
        raise HTTPException(status_code=400, detail="persona is required")

    def _iter() -> Any:
        import json as _json
        try:
            yield (_json.dumps({"type": "status", "message": "select_settings"}) + "\n").encode()
            settings = select_settings(persona)
            yield (_json.dumps({"type": "settings", "data": settings}) + "\n").encode()

            yield (_json.dumps({"type": "status", "message": "gen_questions"}) + "\n").encode()
            questions = gen_questions(persona, settings)
            yield (_json.dumps({"type": "questions", "data": questions}) + "\n").encode()

            yield (_json.dumps({"type": "status", "message": "gen_answers"}) + "\n").encode()
            task_to_qa = gen_answers(persona, questions, model="gpt-4o-mini")
            # stream each QA for chat-style rendering
            for task, qa_list in task_to_qa.items():
                for q, a in qa_list:
                    yield (_json.dumps({"type": "qa", "task": task, "question": q, "answer": a}) + "\n").encode()
            yield (_json.dumps({"type": "qa_done"}) + "\n").encode()

            yield (_json.dumps({"type": "status", "message": "score_answers"}) + "\n").encode()
            scores = score_answers(persona, task_to_qa)
            persona_score = sum(scores.values()) / max(len(scores.keys()), 1)
            yield (_json.dumps({"type": "scores", "scores": scores, "personaScore": persona_score}) + "\n").encode()
            yield (_json.dumps({"type": "done"}) + "\n").encode()
        except Exception as e:
            yield (_json.dumps({"type": "error", "error": str(e)}) + "\n").encode()

    return StreamingResponse(_iter(), media_type="application/x-ndjson")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()


