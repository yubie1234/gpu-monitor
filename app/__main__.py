"""`python -m app` 런처."""

import uvicorn

from app.config import get_settings


def main():
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
