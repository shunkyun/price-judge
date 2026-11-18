# Vercel（Python Serverless）用エントリ。Vercel では DB が永続化されないため DB_PATH=/tmp/prices.db を推奨。
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/prices.db")
from app.main import app  # noqa: E402,F401
