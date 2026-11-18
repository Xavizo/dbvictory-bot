"""Launch stdio MCP from a stable working directory, without starting the bot."""

import os
from pathlib import Path
import runpy

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    os.chdir(root)
    runpy.run_path(str(root / "mcp_server.py"), run_name="__main__")
