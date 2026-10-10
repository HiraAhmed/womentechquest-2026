"""The only place that talks to a model. Allowed models only (competition rule)."""
import base64, hashlib, io, json, os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps

load_dotenv()
ALLOWED = {"gpt-4.1-mini", "gpt-5-mini"}  # OpenAI subset of the permitted list
MODEL = os.getenv("MODEL_NAME", "gpt-5-mini")
if os.getenv("MODEL_PROVIDER", "openai") != "openai" or MODEL not in ALLOWED:
    raise SystemExit(f"Unsupported provider/model. Use openai with one of {sorted(ALLOWED)}.")

_client = OpenAI(api_key=os.getenv("API_KEY"), max_retries=4, timeout=180)
CACHE = Path(".cache")
PRICES = {"gpt-5-mini": (0.25, 2.00), "gpt-4.1-mini": (0.40, 1.60)}  # USD per 1M tokens (in, out), from the guide
USAGE = {"calls": 0, "cached": 0, "input": 0, "output": 0}


def cost_usd() -> float:
    pin, pout = PRICES[MODEL]
    return (USAGE["input"] * pin + USAGE["output"] * pout) / 1e6


def image_data_url(path: Path, max_side: int = 2200) -> str:
    """Fix phone rotation (EXIF), cap the size, and encode for the vision model."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def ask_json(system: str, user: str, image: str | None = None) -> dict:
    """One JSON-mode call; responses are cached on disk so reruns are free and stable."""
    key = hashlib.sha256(f"{MODEL}|{system}|{user}|{image}".encode()).hexdigest()
    f = CACHE / f"{key}.json"
    if f.exists():
        USAGE["cached"] += 1
        return json.loads(f.read_text("utf-8"))
    content = [{"type": "text", "text": user}]
    if image:
        content.append({"type": "image_url", "image_url": {"url": image, "detail": "high"}})
    resp = _client.chat.completions.create(
        model=MODEL,  # reasoning model: no temperature / max_tokens
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
    )
    USAGE["calls"] += 1
    USAGE["input"] += resp.usage.prompt_tokens
    USAGE["output"] += resp.usage.completion_tokens
    data = json.loads(resp.choices[0].message.content)
    CACHE.mkdir(exist_ok=True)
    f.write_text(json.dumps(data), "utf-8")
    return data
