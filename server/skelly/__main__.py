import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(level=os.environ.get("SKELLY_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("skelly.api:app", host=os.environ.get("SKELLY_HOST", "0.0.0.0"),
                port=int(os.environ.get("SKELLY_PORT", "8420")), log_level="info")


if __name__ == "__main__":
    main()
