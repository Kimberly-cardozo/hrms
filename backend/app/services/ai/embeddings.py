from openai import AsyncOpenAI

from app.core.config import settings


class EmbeddingError(RuntimeError):
    pass


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if not settings.openai_api_key.strip():
        raise EmbeddingError("The policy assistant is not configured with an OpenAI API key.")

    vectors: list[list[float] | None] = [None] * len(texts)
    try:
        async with AsyncOpenAI(api_key=settings.openai_api_key) as client:
            for start in range(0, len(texts), 64):
                batch = texts[start : start + 64]
                response = await client.embeddings.create(
                    model=settings.openai_embedding_model,
                    input=batch,
                )
                for item in response.data:
                    vectors[start + item.index] = item.embedding
    except Exception as error:
        raise EmbeddingError("The policy documents could not be embedded.") from error

    if any(vector is None for vector in vectors):
        raise EmbeddingError("The embedding provider returned an incomplete response.")
    return [vector for vector in vectors if vector is not None]


async def embed_query(text: str) -> list[float]:
    vectors = await embed_texts([text])
    return vectors[0]
