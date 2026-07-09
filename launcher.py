import subprocess
import sys
import time
from datetime import datetime

RESTART_DELAY_SECONDS = 30

while True:
    print("\nStarting options bot...")

    result = subprocess.run([sys.executable, "main.py"])

    print(f"Options bot exited with return code: {result.returncode}")

    if result.returncode == 0:
        print("Options bot exited normally. Not restarting.")
        break

    with open("crash.log", "a") as file:
        file.write(
            f"{datetime.now()} - Options bot crashed with return code {result.returncode}\n"
        )

    print(f"Options bot crashed. Restarting in {RESTART_DELAY_SECONDS} seconds...")
    time.sleep(RESTART_DELAY_SECONDS)