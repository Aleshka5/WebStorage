import os
import subprocess
import sys

from loguru import logger


def main() -> None:
    logger.info("Applying database migrations")
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    logger.info("Starting application")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
