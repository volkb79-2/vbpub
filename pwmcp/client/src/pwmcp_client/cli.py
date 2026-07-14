from __future__ import annotations

import argparse
import json

from .contract import load_contract
from .session import verify_installed_playwright


def main() -> None:
    parser = argparse.ArgumentParser(prog="pwmcp")
    parser.add_argument("command", choices=["doctor", "contract"])
    parser.add_argument("--contract", default="http://pwmcp:3000/contract")
    args = parser.parse_args()
    contract = load_contract(args.contract)
    if args.command == "doctor":
        installed = verify_installed_playwright(contract)
        print(f"ok release={contract.release} playwright={installed} ws={contract.ws_url}")
    else:
        print(json.dumps(contract.__dict__, indent=2, sort_keys=True))
