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
from datetime import datetime, timezone
import torch
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

FRESHNESS_TTL_HOURS = {
    "static": 24 * 7,       # 7 days
    "slowly_changing": 6,   # 6 hours
    "dynamic": 0.08,        # ~5 minutes
    "personal": 0,          # never reused across sessions
}

def normalize(text: str) -> str:
    cleaned = text.lower().strip()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned
FILLER_PHRASES = [
    "i was wondering if", "i was thinking", "could you maybe", "can you please",
    "i have an exam", "i have a deadline", "i have a project", "i'm a student",
    "i am a student", "i'm a final year student", "i'm not very good at",
    "i am not very good at", "because i need", "if that's okay",
    "if that's okay with you", "if possible", "if you don't mind", "i really need",
    "i would really appreciate", "thanks in advance", "sorry to bother you",
    "i hope this makes sense", "just wondering", "i guess", "kind of", "sort of",
    "to be honest", "honestly", "basically", "i mean", "i'm a bit stressed",
    "hi", "hey", "hello",
]
VAGUE_INDICATORS = [
    "something about", "stuff about", "things about", "help me with this",
    "explain this", "tell me about this", "what about", "can you help",
]

_FILLER_RE = [(p, re.compile(rf"\b{re.escape(p)}\b")) for p in FILLER_PHRASES]
_VAGUE_RE = [(p, re.compile(rf"\b{re.escape(p)}\b")) for p in VAGUE_INDICATORS]
MIN_WORDS_FOR_VAGUE_REWRITE = 3
def analyze_prompt_locally(message: str) -> dict:
    word_count = len(message.split())
    lower_message = message.lower()

    filler_found = [p for p, rx in _FILLER_RE if rx.search(lower_message)]
    vague_found = [p for p, rx in _VAGUE_RE if rx.search(lower_message)]

    is_verbose = word_count > 30 and len(filler_found) >= 2
    is_vague = word_count < 8 and len(vague_found) >= 1
    if is_vague and word_count <= MIN_WORDS_FOR_VAGUE_REWRITE:
     is_vague = False

    needs_optimization = is_verbose or is_vague

    if is_verbose:
        reason = "verbose"
    elif is_vague:
        reason = "vague"
    else:
        reason = "none"

    return {
        "word_count": word_count,
        "filler_found": filler_found,
        "vague_found": vague_found,
        "needs_optimization": needs_optimization,
        "reason": reason
    }
def get_prompt_optimization(message: str, reason: str) -> dict:
    if reason == "verbose":
        instruction = (
    "Rewrite the user's message as a short, direct request. "
    "Remove greetings, politeness padding, hedging, and personal "
    "circumstances (being a student, deadlines, stress). "
    "Keep the actual task and any constraints that change what a good "
    "answer looks like (for example: 'simple', 'with examples', "
    "'for beginners'). The result must be clearly shorter than the original."
)
    else:
        instruction = (
            "The user's message is short and vague/underspecified. Suggest a slightly "
            "more specific version that would get a better answer, WITHOUT inventing "
            "assumptions the user didn't imply. If there's genuinely not enough "
            "information to improve it meaningfully, return the original unchanged."
        )

    prompt = (
        f"{instruction}\n\n"
        f'Original message: "{message}"\n\n'
        "Respond with ONLY the rewritten message, no explanation, no quotes, no extra text."
    )

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
            json={
                "model": "openrouter/free",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=10
        )
        response.raise_for_status()

        data = response.json()
        suggestion = data["choices"][0]["message"]["content"]

        if not suggestion:
            return {"suggestion": None}

        suggestion = re.sub(r"<think>.*?</think>", "", suggestion, flags=re.IGNORECASE | re.DOTALL)
        suggestion = re.sub(r"</?think>|</?tool_call>", "", suggestion, flags=re.IGNORECASE).strip()

        if not suggestion or suggestion.lower() == message.lower():
            return {"suggestion": None}
        if reason == "verbose" and len(suggestion.split()) >= 0.7 * len(message.split()):
          return {"suggestion": None}
        original_embedding = model.encode(message, convert_to_tensor=True)
        suggestion_embedding = model.encode(suggestion, convert_to_tensor=True)
        similarity = util.cos_sim(original_embedding, suggestion_embedding).item()

        if similarity < 0.6:
            return {"suggestion": None}

        return {
            "suggestion": suggestion,
            "similarity": round(similarity, 2)
        }

    except Exception:
        return {"suggestion": None}
def extract_key_terms(text: str) -> list:
    numbers = re.findall(r"\d+\.?\d*", text)

    words = text.split()
    proper_nouns = [
        word.strip(".,!?;:\"'()")
        for i, word in enumerate(words)
        if i > 0
        and word[:1].isupper()
        and word.lower() not in ["i", "what", "who", "where", "when", "why", "how"]
    ]

    return list(set(numbers + proper_nouns))
PII_PATTERNS = {
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "phone": r"\b\d{10}\b",
    "pan": r"\b[A-Z]{5}\d{4}[A-Z]\b",
    "aadhaar": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
    "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    "date_of_birth": r"\b\d{1,2}[\s/-](?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|\d{1,2})[\s/-]\d{2,4}\b",
    "student_id": r"\b[A-Z]{2,5}\d{4}-\d{3,6}\b",
}

SECRET_PATTERNS = {
    "api_key_generic": r"\b(?:sk|pk|api)[-_][a-zA-Z0-9]{16,}\b",
    "aws_key": r"\bAKIA[0-9A-Z]{16}\b",
    "github_token": r"\bghp_[a-zA-Z0-9]{36}\b",
    "generic_secret": r"(?i)\b(?:secret|token|api[_-]?key)\s*[:=]\s*[a-zA-Z0-9\-_]{12,}\b",
    "password_field": r"(?i)\bpassword\b[\s\S]{0,25}?(?::|=|\bis\b|\bwas\b)\s*\S+",
    "embedded_secret_identifier": r"(?i)[A-Z0-9_]*(?:API[_-]?KEY|ACCESS[_-]?TOKEN|SECRET[_-]?KEY)[A-Z0-9_]*",
}

RISK_WEIGHTS = {
    "email": 20,
    "phone": 30,
    "pan": 60,
    "aadhaar": 80,
    "credit_card": 90,
    "date_of_birth": 40,
    "student_id": 35,
    "api_key_generic": 95,
    "password_field": 95,
    "aws_key": 100,
    "github_token": 100,
    "generic_secret": 90,
    "embedded_secret_identifier": 90,
}


def detect_sensitive_data(text: str) -> dict:
    findings = {}

    for label, pattern in {**PII_PATTERNS, **SECRET_PATTERNS}.items():
        matches = re.findall(pattern, text)
        if matches:
            findings[label] = matches

    risk_score = 0
    for label in findings:
        risk_score = max(risk_score, RISK_WEIGHTS.get(label, 50))

    if risk_score >= 81:
        risk_level = "critical"
    elif risk_score >= 51:
        risk_level = "high"
    elif risk_score >= 21:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "findings": findings,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "has_sensitive_data": len(findings) > 0
    }
def mask_sensitive_data(text: str, findings: dict) -> tuple:
    masked_text = text
    mapping = {}
    counter_by_type = {}

    for label, matches in findings.items():
        for match in matches:
            counter_by_type[label] = counter_by_type.get(label, 0) + 1
            placeholder = f"[{label.upper()}_{counter_by_type[label]}]"

            mapping[placeholder] = match
            masked_text = masked_text.replace(match, placeholder)

    return masked_text, mapping


def unmask_response(text: str, mapping: dict) -> str:
    restored_text = text
    for placeholder, original_value in mapping.items():
        restored_text = restored_text.replace(placeholder, original_value)

    return restored_text

def classify_freshness(message: str) -> str:
    prompt = (
        "Classify this question into exactly ONE of these categories:\n"
        "STATIC - facts that essentially never change (e.g. scientific definitions, "
        "historical events, how something works, math concepts)\n"
        "SLOWLY_CHANGING - facts that change occasionally, over months/years "
        "(e.g. who holds a position/role, company leadership, population figures)\n"
        "DYNAMIC - facts that change frequently, within hours/minutes "
        "(e.g. prices, weather, live scores, current events, today's date)\n"
        "PERSONAL - questions about the specific user's own data/context "
        "(e.g. 'my balance', 'my appointments', 'what did I just ask')\n\n"
        f'Question: "{message}"\n\n'
        "Reply with ONLY one word: STATIC, SLOWLY_CHANGING, DYNAMIC, or PERSONAL. "
        "No explanation, no punctuation."
    )

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
            json={
                "model": "openrouter/free",
                "messages": [{"role": "user", "content": prompt}]
            }
        )

        data = response.json()
        raw = data["choices"][0]["message"]["content"].strip().upper()

        raw = re.sub(r"[^A-Z_]", "", raw)

        if "STATIC" in raw:
            return "static"
        elif "SLOWLY" in raw:
            return "slowly_changing"
        elif "DYNAMIC" in raw:
            return "dynamic"
        elif "PERSONAL" in raw:
            return "personal"
        else:
            return "slowly_changing"

    except Exception:
        return "slowly_changing"
    
def get_structured_response(message: str, history: list) -> dict:
    is_code_request = any(
        keyword in message.lower()
        for keyword in [
            "write code", "write a code", "write a program", "write a function",
            "code for", "program for", "script for", "function to",
            "whole code", "full code", "complete code", "entire code"
        ]
    )

    wants_detail = any(
        keyword in message.lower()
        for keyword in [
            "in detail", "in-depth", "in depth", "explain in detail", "elaborate",
            "thoroughly", "comprehensive", "comprehensively", "detailed explanation",
            "go in depth", "deep dive", "with examples", "with details",
            "full explanation", "extensively", "at length", "step by step",
            "step-by-step", "all the details", "everything about", "walk me through",
            "give me a detailed", "give a detailed", "explain thoroughly",
            "long answer", "longer answer", "give more detail", "more detailed"
        ]
    )

    wants_brief = any(
        keyword in message.lower()
        for keyword in [
            "in short", "briefly", "brief answer", "short answer", "in brief",
            "quick summary", "quickly summarize", "summarize briefly", "tl;dr",
            "in one line", "in a sentence", "short and simple", "keep it short",
            "keep it brief", "one-line", "one line answer", "simple answer",
            "just the answer", "concise answer", "to the point", "short version",
            "give a quick", "quick answer", "in simple terms", "simply put"
        ]
    )

    if is_code_request:
        length_instruction = (
            "short_answer: the ACTUAL working code the user asked for, inside a markdown code "
            "block (```python ... ```), with at most 1 short sentence of context before it. "
            "Do NOT just describe the code in words — write the real code."
        )
    elif wants_detail:
        length_instruction = (
            "short_answer: the user has explicitly asked for a detailed/thorough/in-depth "
            "explanation. Provide a genuinely comprehensive answer — multiple paragraphs, "
            "covering the topic properly with examples where relevant. Do NOT artificially "
            "shorten this just because the field is called 'short_answer' — the field name "
            "is just a label, length should match what the user asked for."
        )
    elif wants_brief:
        length_instruction = (
            "short_answer: the user has explicitly asked for a brief/short/quick answer. "
            "Give exactly 1 sentence, as concise as possible while still being correct."
        )
    else:
        length_instruction = (
            "short_answer: 1-3 sentences, direct and concise, since the user didn't specify "
            "a length preference."
        )

    if is_code_request:
        system_prompt = (
            "You are an assistant. Respond ONLY in valid JSON, no extra text, no markdown fences "
            "around the JSON itself. "
            "Format: {\"short_answer\": \"...\", \"suggested_facets\": [\"...\", \"...\"]}. "
            f"{length_instruction} "
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
            f"{length_instruction} "
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

    raw_text = None
    for attempt in range(2):  # one retry, since free models fail intermittently
        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
                json={
                    "model": "openrouter/free",
                    "messages": messages
                },
                timeout=30
            )
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            print(f"OpenRouter request failed (attempt {attempt + 1}): {e}")
            continue

        choices = data.get("choices")
        if choices:
            raw_text = choices[0]["message"]["content"]
            break

        # No "choices": OpenRouter sent an error body, so log it to see why
        print(f"OpenRouter error (attempt {attempt + 1}): {data}")

    if raw_text is None:
        return {
            "short_answer": "The AI model didn't return a usable response for this request. This can happen occasionally with free-tier models. Please try asking again.",
            "suggested_facets": []
        }

    print("=== RAW AI OUTPUT ===")
    print(raw_text)
    print("=== END RAW OUTPUT ===")

    raw_text = re.sub(r"</?think>|</?tool_call>", "", raw_text, flags=re.IGNORECASE)

    lines = raw_text.strip().split("\n")
    lines = [
        line for line in lines
        if not line.strip().lower().startswith(("user safety", "response safety", "safety:"))
    ]
    raw_text = "\n".join(lines).strip()
    if not raw_text:
        return {
            "short_answer": "The AI model didn't return a usable response for this request. This can happen occasionally with free-tier models, especially for sensitive-sounding prompts. Please try rephrasing your question.",
            "suggested_facets": []
        }
        answer_match = re.search(
        r'"short_answer"\s*:\s*"(.*?)"\s*,\s*"suggested_facets"',
        raw_text, re.DOTALL
    )
    facets_match = re.search(
        r'"suggested_facets"\s*:\s*(\[.*?\])',
        raw_text, re.DOTALL
    )

    if not facets_match:
        facets_match = re.search(
            r'suggested_facets\s*:\s*(\[.*?\])',
            raw_text, re.IGNORECASE | re.DOTALL
        )

    if not raw_text:
        return {
          "short_answer": "The AI model didn't return a usable response for this request. This can happen occasionally with free-tier models, especially for sensitive-sounding prompts. Please try rephrasing your question.",
            "suggested_facets": []
        
        }

    # 1) Try clean JSON parsing first (strip ```json fences if the model added them)
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.IGNORECASE)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict) and "short_answer" in parsed:
            facets = parsed.get("suggested_facets", [])
            if not isinstance(facets, list):
                facets = []
            return {
                "short_answer": str(parsed["short_answer"]),
                "suggested_facets": facets
            }
    except Exception:
        pass

    # 2) Fallback: regex extraction for malformed JSON
    answer_match = re.search(
        r'"short_answer"\s*:\s*"(.*?)"\s*,\s*"suggested_facets"',
        raw_text, re.DOTALL
    )
    facets_match = re.search(
        r'"suggested_facets"\s*:\s*(\[.*?\])',
        raw_text, re.DOTALL
    )

    if not facets_match:
        facets_match = re.search(
            r'suggested_facets\s*:\s*(\[.*?\])',
            raw_text, re.IGNORECASE | re.DOTALL
        )

    short_answer_loose = None
    if not answer_match:
        loose_answer_match = re.search(
            r'short_answer\s*:\s*(.*?)(?:\n\s*suggested_facets|\Z)',
            raw_text, re.IGNORECASE | re.DOTALL
        )
        if loose_answer_match:
            short_answer_loose = loose_answer_match.group(1).strip().strip('"').strip()

    if answer_match:
        short_answer = answer_match.group(1)
        short_answer = short_answer.replace("\\n", "\n").replace('\\"', '"')
    elif short_answer_loose:
        short_answer = short_answer_loose
    else:
        short_answer = raw_text.strip()

    facets = []
    if facets_match:
        try:
            facets = json.loads(facets_match.group(1))
        except Exception:
            facets = []

    return {
        "short_answer": short_answer,
        "suggested_facets": facets
    }
def calculate_entity_match(new_terms: list, old_terms: list) -> float:
    if not new_terms and not old_terms:
        return 1.0
    if not new_terms or not old_terms:
        return 0.5

    new_set = set(t.lower() for t in new_terms)
    old_set = set(t.lower() for t in old_terms)

    overlap = len(new_set & old_set)
    total = len(new_set | old_set)

    return overlap / total if total > 0 else 0.5


def is_expired(created_at, freshness_class: str) -> bool:
    ttl_hours = FRESHNESS_TTL_HOURS.get(freshness_class, 6)
    age_hours = (datetime.now(timezone.utc) - created_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
    return age_hours > ttl_hours

def find_cached_answer(message: str):
    with Session(engine) as db:
        normalized = normalize(message)
        exact_match = (
            db.query(Chat)
            .filter(Chat.normalized_question == normalized, Chat.freshness_class != "personal")
            .order_by(Chat.id.desc())
            .first()
        )

        if exact_match and not is_expired(exact_match.created_at, exact_match.freshness_class):
            print("✅ EXACT CACHE HIT")
            return exact_match.answer, exact_match.freshness_class

        freshness_class = classify_freshness(message)

        if freshness_class == "personal":
            return None, freshness_class

        new_embedding = model.encode(message, convert_to_tensor=True)
        new_terms = extract_key_terms(message)

        past_chats = db.query(Chat).filter(Chat.freshness_class != "personal").all()

        best_match = None
        best_score = 0

        for old_chat in past_chats:
            if not old_chat.embedding or not old_chat.freshness_class:
                continue

            if is_expired(old_chat.created_at, old_chat.freshness_class):
                continue

            old_embedding_list = json.loads(old_chat.embedding)
            old_embedding = torch.tensor(old_embedding_list)
            semantic_sim = util.cos_sim(new_embedding, old_embedding).item()
            if semantic_sim < 0.75:
                continue

            old_terms = json.loads(old_chat.key_terms) if old_chat.key_terms else []
            entity_sim = calculate_entity_match(new_terms, old_terms)

            reuse_score = (0.7 * semantic_sim) + (0.3 * entity_sim)

            if reuse_score > best_score:
                best_score = reuse_score
                best_match = old_chat

        if best_match and best_score >= 0.85:
            print(f"✅ SEMANTIC CACHE HIT (score: {best_score:.2f})")
            return best_match.answer, freshness_class

        print("🔵 CACHE MISS")
        return None, freshness_class

@app.post("/chat")
def chat(data: dict):
    message = data.get("message", "")

    privacy_result = detect_sensitive_data(message)
    mapping = {}
    if privacy_result["has_sensitive_data"]:
        safe_message, mapping = mask_sensitive_data(message, privacy_result["findings"])
    else:
        safe_message = message

    prompt_analysis = analyze_prompt_locally(safe_message)
    optimization_suggestion = None

    if prompt_analysis["needs_optimization"]:
        opt_result = get_prompt_optimization(safe_message, prompt_analysis["reason"])
        if opt_result.get("suggestion"):
            optimization_suggestion = {
                "original": message,
                "suggested": unmask_response(opt_result["suggestion"], mapping),
                "reason": prompt_analysis["reason"],
                "similarity": opt_result.get("similarity")
            }

    cached_answer, freshness_class = find_cached_answer(safe_message)

    if cached_answer:
        answer = cached_answer
        facets = []
        embedding_json = None
        key_terms_json = None
    else:
        with Session(engine) as db_temp:
            recent_chats = db_temp.query(Chat).order_by(Chat.id.desc()).limit(4).all()
            recent_chats.reverse()
            history = [{"question": c.question, "answer": c.answer} for c in recent_chats]

        result = get_structured_response(safe_message, history)
        answer = result["short_answer"]
        facets = result["suggested_facets"]

        new_embedding = model.encode(safe_message, convert_to_tensor=True)
        embedding_json = json.dumps(new_embedding.tolist())
        key_terms_json = json.dumps(extract_key_terms(safe_message))

    if mapping:
        answer = unmask_response(answer, mapping)

    with Session(engine) as db:
        chat = Chat(
            question=safe_message,
            normalized_question=normalize(safe_message),
            answer=answer,
            embedding=embedding_json,
            freshness_class=freshness_class,
            key_terms=key_terms_json
        )
        db.add(chat)
        db.commit()

    return {
        "response": answer,
        "facets": facets,
        "privacy": {
            "has_sensitive_data": privacy_result["has_sensitive_data"],
            "risk_level": privacy_result["risk_level"],
            "detected_types": list(privacy_result["findings"].keys())
        },
        "prompt_optimization": optimization_suggestion
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
    choices = result.get("choices")
    if not choices or not choices[0]["message"]["content"]:
        return {"facet": facet, "content": "The AI service returned an error. Please try again."}
    content = choices[0]["message"]["content"]

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
    with Session(engine) as db:
        chats = db.query(Chat).order_by(Chat.id).all()

        return [
            {"question": c.question, "answer": c.answer}
            for c in chats
        ]