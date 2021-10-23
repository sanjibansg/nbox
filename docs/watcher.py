#!/usr/bin/env python3

# simple server that takes a POST request, and echos the body back written in FastAPI

import subprocess
import fastapi as fa
from time import time
from pydantic import BaseModel

class Echo(BaseModel):
    data: str = ""

app = fa.FastAPI()

de_bounce = time()

@app.post("/")
async def echo(echo: Echo):
  print(echo)
  print("Updated file:", echo.data)
  global de_bounce
  if time() - de_bounce > 5:
    de_bounce = time()
    subprocess.run(["make", "html"])
  return echo

if __name__ == "__main__":
  import uvicorn
  uvicorn.run(app, host="0.0.0.0", port=6942)
