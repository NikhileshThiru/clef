import uvicorn

from . import config

config.load_env()

if __name__ == "__main__":
    uvicorn.run("clefd.app:app", host=config.HOST, port=config.PORT, log_level="warning")
