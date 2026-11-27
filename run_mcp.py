"""Launch stdio MCP from a stable working directory, without starting the bot."""

import os
from pathlib import Path
import runpy
import sys

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    runpy.run_module("dbv.mcp_server.server", run_name="__main__", alter_sys=True)
