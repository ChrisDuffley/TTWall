from __future__ import annotations

import argparse

from .config import default_config_path, write_default_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ttwall", description="TeamTalk CLI client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-config", help="Write a starter INI configuration file")
    init_parser.add_argument("--config", default=str(default_config_path()), help="Path to the INI file")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-config":
        path = write_default_config(path=None if args.config == str(default_config_path()) else __import__("pathlib").Path(args.config))
        print(f"Wrote config to {path}")
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2
