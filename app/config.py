import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    HOST = os.getenv("MLINZI_HOST", "127.0.0.1")
    PORT = int(os.getenv("MLINZI_PORT", "9000"))
    ECHO_PORT = int(os.getenv("MLINZI_ECHO_PORT", "9001"))
    REQUEST_TIMEOUT = float(os.getenv("MLINZI_REQUEST_TIMEOUT", "5.0"))
    LOG_LEVEL = os.getenv("MLINZI_LOG_LEVEL", "INFO")