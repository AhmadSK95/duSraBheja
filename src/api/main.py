"""API entrypoint for local and container runs."""

import uvicorn

from src.config import settings


def main() -> None:
    uvicorn.run("src.api.app:app", host=settings.api_host, port=settings.api_port, reload=False)


if __name__ == "__main__":
    main()
