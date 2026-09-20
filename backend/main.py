from models import Chat
from database import engine
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import requests
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer, util
import re

load_dotenv()

app = FastAPI()

model = SentenceTransformer("all-MiniLM-L6-v2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_structured_response(message: str, history: list) -> dict:
    is_code_request = any(
        keyword in message.lower()
        for keyword in [
            "write code", "write a code", "write a program", "write a function",
            "code for", "program for", "script for", "function to",
            "whole code", "full code", "complete code", "entire code"
        ]
    )

    if is_code_request:
        system_prompt = (
            "You are an assistant. Respond ONLY in valid JSON, no extra text, no markdown fences "
            "around the JSON itself. "
            "Format: {\"short_answer\": \"...\", \"suggested_facets\": [\"...\", \"...\"]}. "
            "short_answer: the ACTUAL working code the user asked for, inside a markdown code "
            "block (```python ... ```), with at most 1 short sentence of context before it. "
            "Do NOT just describe the code in words — write the real code. "
            "Use the conversation history to understand follow-up requests like 'whole program' "
            "or 'now add error handling' — they refer to what was just discussed. "
            "suggested_facets: 2-4 short labels for genuinely relevant follow-ups "
            "(examples: 'Explanation', 'Time complexity', 'Alternative method', 'Edge cases'). "
            "If nothing meaningful applies, return an empty list."
        )
    else:
        system_prompt = (
            "You are an assistant. Respond ONLY in valid JSON, no extra text, no markdown fences. "
            "Format: {\"short_answer\": \"...\", \"suggested_facets\": [\"...\", \"...\"]}. "
            "short_answer: 1-3 sentences, direct and concise. "
            "Use the conversation history to understand follow-up questions that refer to "
            "what was just discussed. "
            "suggested_facets: 2-4 short labels (2-4 words each) for follow-up details that "
            "genuinely make sense for THIS specific question (examples: 'Examples', "
            "'Advantages', 'Disadvantages', 'History', 'Calculation steps', 'Nearby places'). "
            "Only suggest facets relevant to this question's topic. "
            "If nothing meaningful applies, return an empty list."
        )

    messages = [{"role": "system", "content": system_prompt}]

    for past in history:
        messages.append({"role": "user", "content": past["question"]})
        messages.append({"role": "assistant", "content": past["answer"]})

    messages.append({"role": "user", "content": message})

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
        json={
            "model": "openrouter/free",
            "messages": messages
        }
    )

    data = response.json()
    raw_text = data["choices"][0]["message"]["content"]

    raw_text = raw_text.replace("\\n", "\n")
    raw_text = raw_text.replace("\\n", "\n")

    lines = raw_text.strip().split("\n")
    lines = [
        line for line in lines
        if not line.strip().lower().startswith(("user safety", "response safety", "safety:"))
    ]
    raw_text = "\n".join(lines).strip()

    try:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object found")

        json_slice = raw_text[start:end + 1]
        parsed = json.loads(json_slice)

        return {
            "short_answer": parsed.get("short_answer", raw_text),
            "suggested_facets": parsed.get("suggested_facets", [])
        }
    except Exception:
        facet_match = re.search(r"Suggested Facets:\s*(\[.*?\])", raw_text, re.IGNORECASE | re.DOTALL)

        if facet_match:
            try:
                facets = json.loads(facet_match.group(1).replace("'", '"'))
            except Exception:
                facets = []
            short_answer = raw_text[:facet_match.start()].strip()
        else:
            facets = []
            short_answer = raw_text.strip()

        return {
            "short_answer": short_answer,
            "suggested_facets": facets
        }

def find_similar_answer(message: str):
    db = Session(engine)
    past_chats = db.query(Chat).all()

    if not past_chats:
        return None

    new_embedding = model.encode(message, convert_to_tensor=True)

    for old_chat in past_chats:
        old_embedding = model.encode(old_chat.question, convert_to_tensor=True)
        similarity = util.cos_sim(new_embedding, old_embedding).item()

        if similarity >= 0.85:
            return old_chat.answer

    return None

@app.post("/chat")
def chat(data: dict):
    message = data.get("message", "")

    cached_answer = find_similar_answer(message)

    if cached_answer:
        print("✅ CACHE HIT — reused previous answer")
        answer = cached_answer
        facets = []
    else:
        print("🔵 Calling AI — no similar question found")
        db_temp = Session(engine)
        recent_chats = db_temp.query(Chat).order_by(Chat.id.desc()).limit(4).all()
        recent_chats.reverse()
        history = [{"question": c.question, "answer": c.answer} for c in recent_chats]
        result = get_structured_response(message, history)
        answer = result["short_answer"]
        facets = result["suggested_facets"]

    db = Session(engine)

    chat = Chat(
        question=message,
        answer=answer
    )

    db.add(chat)
    db.commit()

    return {
        "response": answer,
        "facets": facets
    }

@app.post("/expand")
def expand(data: dict):
    question = data.get("question", "")
    facet = data.get("facet", "")

    is_code_facet = any(
        keyword in facet.lower()
        for keyword in ["code", "example", "script", "program", "function", "syntax"]
    )

    if is_code_facet:
        prompt = (
            f'Regarding the question: "{question}", provide the ACTUAL working code '
            f'relevant to: {facet}. Write real, complete, runnable code inside a markdown '
            f'code block. Do not just describe what the code should contain — write it.'
        )
    else:
        prompt = (
            f'Regarding the question: "{question}", provide a focused, '
            f'concise explanation specifically about: {facet}.'
        )

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
        json={
            "model": "openrouter/free",
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    result = response.json()
    content = result["choices"][0]["message"]["content"]

    content = re.sub(r"</?think>|</?tool_call>", "", content, flags=re.IGNORECASE)
    content = content.replace("\\n", "\n")

    lines = content.strip().split("\n")
    lines = [
        line for line in lines
        if not line.strip().lower().startswith(("user safety", "response safety", "safety:"))
    ]
    content = "\n".join(lines).strip()

    return {"facet": facet, "content": content}

@app.get("/history")
def get_history():
    db = Session(engine)
    chats = db.query(Chat).order_by(Chat.id).all()

    return [
        {"question": c.question, "answer": c.answer}
        for c in chats
    ]