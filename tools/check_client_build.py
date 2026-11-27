"""Offline client build check. Same as: python -m dbv.client_bridge.check_client_build"""
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
runpy.run_module("dbv.client_bridge.check_client_build", run_name="__main__", alter_sys=True)
