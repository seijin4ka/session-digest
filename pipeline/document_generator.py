import asyncio
import logging
from pathlib import Path

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DOCUMENT_TYPES = {
    "structured_notes": "構造化ノート",
    "full_transcription": "全文書き起こし + 要約",
    "hands_on_instructions": "ハンズオン手順書",
}

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(doc_type: str) -> str:
    prompt_path = PROMPTS_DIR / f"{doc_type}.md"
    return prompt_path.read_text(encoding="utf-8")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=5, max=60))
async def generate_document(
    client: AsyncOpenAI,
    transcript: str,
    doc_type: str,
) -> str:
    prompt_template = _load_prompt(doc_type)
    prompt = prompt_template.replace("{transcript}", transcript)

    logger.info(f"Generating {doc_type}...")
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=16384,
    )
    return response.choices[0].message.content


async def generate_all(
    client: AsyncOpenAI,
    transcript: str,
) -> dict[str, str]:
    results: dict[str, str] = {}

    async def _generate(doc_type: str):
        try:
            content = await generate_document(client, transcript, doc_type)
            results[doc_type] = content
        except Exception as e:
            logger.error(f"Failed to generate {doc_type}: {e}")
            results[doc_type] = None
            results[f"{doc_type}_error"] = str(e)

    tasks = [_generate(dt) for dt in DOCUMENT_TYPES]
    await asyncio.gather(*tasks)

    return results
