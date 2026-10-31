"""Console entry point: uvicorn launcher with honest warnings."""

from __future__ import annotations

import logging
import os

from .api import create_app
from .config import Settings
from .observability import configure_logging


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info(
        "starting Voxora Studio",
        extra={"telephony": "simulated", "live_calls_allowed": settings.allow_live_calls},
    )
    import uvicorn

    app = create_app(settings)
    uvicorn.run(
        app,
        host=os.getenv("VOXORA_HOST", "127.0.0.1"),
        port=int(os.getenv("VOXORA_PORT", "8000")),
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
