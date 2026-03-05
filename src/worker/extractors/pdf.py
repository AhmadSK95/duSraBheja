"""PDF text extraction via pymupdf4llm."""

import asyncio


async def extract_pdf(file_path: str) -> str:
    """Extract text from PDF as clean markdown."""

    def _extract(path: str) -> str:
        import pymupdf4llm

        return pymupdf4llm.to_markdown(path)

    # Run in thread pool since pymupdf is synchronous
    return await asyncio.get_event_loop().run_in_executor(None, _extract, file_path)
