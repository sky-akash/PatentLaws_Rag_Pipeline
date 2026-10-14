"""A small client for OpenAI-compatible chat APIs (Groq by default; Ollama works too) that returns JSON matching a schema."""
import json, re, time
import httpx
from . import config

class LLMError(Exception):
    pass

class RateLimited(LLMError):
    def __init__(self, msg, wait: float):
        super().__init__(msg); self.wait = wait

class ChatClient:
    def __init__(self, url: str = config.GROQ_URL, key: str = config.GROQ_API_KEY):
        self.url, self.key = url.rstrip("/"), key

    @property
    def ready(self) -> bool:
        # Groq needs a key; a local server (Ollama) doesn't
        return bool(self.key) or "localhost" in self.url or "127.0.0.1" in self.url

    def json(self, model: str, system: str, user: str, schema: dict, name: str, max_tokens: int = 2000,
             reasoning: str | None = "low", max_wait: float = 30) -> tuple[dict, dict]:
        # one chat call whose reply must be JSON of the given schema; returns (the parsed reply, token usage)
        if not self.ready: raise LLMError("No GROQ_API_KEY: paste your key into the .env file in the project folder.")
        body = {"model": model, "temperature": 0.1, "max_completion_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                # strict: the server constrains decoding to the schema, so the reply always parses
                "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
        # gpt-oss models think before answering; 'low' keeps the hidden reasoning (which also counts against the
        # free tier's tokens-per-minute) short
        if reasoning and "gpt-oss" in model: body["reasoning_effort"] = reasoning
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        for attempt in range(3):
            try:
                r = httpx.post(f"{self.url}/chat/completions", json=body, headers=headers, timeout=120)
            except httpx.HTTPError as e:
                raise LLMError(f"Could not reach the model server: {e}") from e
            # rate limited: wait as long as the server says if that's short, otherwise tell the caller
            if r.status_code == 429:
                wait = _retry_after(r)
                if wait <= max_wait and attempt < 2: time.sleep(wait + 0.5); continue
                raise RateLimited(f"{model} is rate-limited for {wait:.0f}s (Groq free tier).", wait)
            if r.status_code == 401: raise LLMError("Groq rejected the API key - check GROQ_API_KEY in .env.")
            # strict mode can still fail server-side when the model strays from the schema: sample again
            if r.status_code == 400 and "does not match the expected schema" in r.text and attempt < 2: continue
            if r.status_code >= 400: raise LLMError(f"{model} answered {r.status_code}: {r.text[:300]}")
            data = r.json()
            text = data["choices"][0]["message"].get("content") or ""
            try:
                return json.loads(text), data.get("usage", {})
            except json.JSONDecodeError:
                # strict mode makes this rare; one retry, then give up clearly
                if attempt < 2: continue
                raise LLMError(f"{model} did not return valid JSON.")
        raise LLMError(f"{model}: no usable answer after retries.")

def _retry_after(r: httpx.Response) -> float:
    # Groq sends 'retry-after' in seconds, or describes the wait in the message ('try again in 7.5s' / '1m2.3s')
    h = r.headers.get("retry-after")
    if h:
        try: return float(h)
        except ValueError: pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", r.text)
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 20.0


######
# LLM module: talks to Groq (or any OpenAI-compatible server, e.g. Ollama at http://localhost:11434/v1) and always
# asks for JSON in strict schema mode, so answers arrive as structured data the app can check field by field.
# It waits out short rate limits by itself and reports long ones (the free tier allows ~8K tokens a minute).
