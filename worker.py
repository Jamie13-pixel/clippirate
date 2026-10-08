import os
from dotenv import load_dotenv
load_dotenv()

SECRET = os.getenv("WORKER_SECRET")

print("WORKER STARTED")

if SECRET:
    print("WORKER SECRET LOADED")
else:
    print("WORKER SECRET MISSING")