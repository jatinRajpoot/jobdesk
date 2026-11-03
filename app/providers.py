"""Small provider boundary: fixed endpoints, bounded requests, safe error messages."""
import asyncio
import json
import re

import httpx

from . import config, db

LIMITS = {"parallel": 150, "river": 100}
LLM_SLOTS = asyncio.Semaphore(2)


class ProviderError(Exception):
    pass


def check_budget(provider):
    with db.connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM usage WHERE provider=? AND time>=?", (provider, db.now()[:10])).fetchone()[0]
    if n >= LIMITS[provider]:
        raise ProviderError(f"Today's local {provider.title()} request limit has been reached. Try again tomorrow.")


async def request_json(url, headers, payload, provider):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(100, connect=15), follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            explanations = {401: "The API key was not accepted. Check Connections.", 402: "The provider account needs credits.", 403: "This account cannot access the requested service.", 404: "The model or API endpoint is unavailable.", 422: "The provider did not accept the request format.", 429: "The provider is rate limiting requests. Try again shortly."}
            raise ProviderError(f"{provider}: " + explanations.get(response.status_code, f"Request failed (HTTP {response.status_code})."))
        return response.json()
    except (httpx.TimeoutException, httpx.NetworkError):
        raise ProviderError(f"{provider}: Connection timed out or is unavailable. Please try again.") from None
    except (json.JSONDecodeError, ValueError):
        raise ProviderError(f"{provider}: The response could not be read.") from None


async def search(queries, objective, limit=8):
    key = config.parallel_key()
    if not key:
        raise ProviderError("Add your Parallel API key in Connections to search the web.")
    check_budget("parallel")
    db.usage("parallel", "search")
    data = await request_json("https://api.parallel.ai/v1/search", {"x-api-key": key}, {"search_queries": queries[:3], "objective": objective, "mode": "basic", "max_chars_total": 18000, "advanced_settings": {"max_results": limit}}, "Parallel")
    return data.get("results", [])[:limit]


async def extract(url):
    key = config.parallel_key()
    if not key:
        raise ProviderError("Add your Parallel API key in Connections to research job links.")
    check_budget("parallel")
    db.usage("parallel", "extract")
    data = await request_json("https://api.parallel.ai/v1/extract", {"x-api-key": key}, {"urls": [url], "objective": "Extract this job's role, employer, requirements, location, application method, posting date and any hiring or payment claims.", "max_chars_total": 16000}, "Parallel")
    return data.get("results", [])


async def llm(system, payload, operation, max_tokens=6000, json_output=True):
    creds = config.river_credentials()
    if not creds["key"]:
        raise ProviderError("A River API key is required. Set RIVER_API_KEY on the server.")
    async with LLM_SLOTS:
        check_budget("river")
        attempt_id = db.usage("river", operation)
        body = {"model": creds["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], "reasoning_effort": "low", "max_tokens": max_tokens, "stream": False}
        if json_output:
            body["response_format"] = {"type": "json_object"}
        response = await request_json(creds["base"] + "/chat/completions", {"Authorization": "Bearer " + creds["key"]}, body, "River / DeepSeek")
        choices = response.get("choices", [])
        if not choices or choices[0].get("finish_reason") == "length":
            raise ProviderError("DeepSeek did not finish its response. Try again with a shorter resume or job description.")
        content = choices[0].get("message", {}).get("content", "")
        usage = response.get("usage", {})
        with db.connection() as conn:
            conn.execute("UPDATE usage SET input_tokens=?, output_tokens=? WHERE id=?", (usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), attempt_id))
        if not json_output:
            return content
        try:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (json.JSONDecodeError, ValueError, TypeError):
            raise ProviderError("DeepSeek returned an incomplete structured response. Nothing was saved; please try again.") from None
