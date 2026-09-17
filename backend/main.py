from models import Chat
from database import engine
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import requests
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Allow requests from the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_ai_response(message: str) -> str:
    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
        },
        json={
            "model": "openrouter/free",
            "messages": [
                {"role": "user", "content": message}
            ]
        }
    )

    data = response.json()
    return data["choices"][0]["message"]["content"]

@app.post("/chat")
def chat(data: dict):

    message = data.get("message", "")

    answer = get_ai_response(message)

    db = Session(engine)

    chat = Chat(
        question=message,
        answer=answer
    )

    db.add(chat)
    db.commit()

    return {
        "response": answer
    }

@app.get("/history")
def get_history():
    db = Session(engine)
    chats = db.query(Chat).order_by(Chat.id).all()

    return [
        {"question": c.question, "answer": c.answer}
        for c in chats
    ]