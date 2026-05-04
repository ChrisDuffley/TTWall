from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import AppConfig, default_config_path, load_config, write_default_config
from .protocol import TeamTalkClient, TeamTalkError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ttwall", description="TeamTalk CLI client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-config", help="Write a starter INI configuration file")
    init_parser.add_argument("--config", default=str(default_config_path()), help="Path to the INI file")

    login_parser = subparsers.add_parser("login", help="Log in to a TeamTalk server")
    _add_common_options(login_parser)
    login_parser.add_argument("--join", action="store_true", help="Join a channel after login")
    login_parser.add_argument("--channel", help="Channel path or channel ID to join")
    login_parser.add_argument("--channel-password", default="", help="Password for the channel to join")

    channel_parser = subparsers.add_parser("send-channel", help="Send a channel message")
    _add_common_options(channel_parser)
    channel_parser.add_argument("message", help="The message content")
    channel_parser.add_argument("--channel", help="Channel path or channel ID to use")
    channel_parser.add_argument("--channel-password", default="", help="Password for the channel if one is required")

    private_parser = subparsers.add_parser("send-private", help="Send a private message")
    _add_common_options(private_parser)
    private_parser.add_argument("user", help="Destination user ID, username, or nickname")
    private_parser.add_argument("message", help="The message content")

    broadcast_parser = subparsers.add_parser("send-broadcast", help="Send a broadcast message")
    _add_common_options(broadcast_parser)
    broadcast_parser.add_argument("message", help="The message content")

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=str(default_config_path()), help="Path to the INI file")
    parser.add_argument("--host", help="Override the configured host")
    parser.add_argument("--tcp-port", type=int, help="Override the configured TCP port")
    parser.add_argument("--encrypted", action="store_true", help="Use TLS for the TeamTalk connection")
    parser.add_argument("--nickname", help="Override the configured nickname")
    parser.add_argument("--username", help="Override the configured username")
    parser.add_argument("--password", help="Override the configured password")
    parser.add_argument("--client-name", help="Override the configured TeamTalk client name")


def _apply_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    if getattr(args, "host", None):
        config.server.host = args.host
    if getattr(args, "tcp_port", None):
        config.server.tcp_port = args.tcp_port
    if getattr(args, "encrypted", False):
        config.server.encrypted = True
    if getattr(args, "nickname", None):
        config.identity.nickname = args.nickname
    if getattr(args, "username", None):
        config.identity.username = args.username
    if getattr(args, "password", None):
        config.identity.password = args.password
    if getattr(args, "client_name", None):
        config.identity.client_name = args.client_name
    return config


def _run_login(args: argparse.Namespace, config: AppConfig) -> int:
    with TeamTalkClient.from_config(config) as client:
        client.connect()
        client.login()
        print(f"Logged in to {client.server_name or config.server.host} as user ID #{client.my_user_id}")

        if args.join or args.channel or config.defaults.auto_join or config.server.channel:
            channel_ref = args.channel or config.server.channel or "/"
            channel_password = args.channel_password or config.server.channel_password
            client.join_channel(channel_ref, channel_password)
            print(f"Joined channel {client.get_channel_path(client.current_channel_id)}")

    return 0


def _run_send_channel(args: argparse.Namespace, config: AppConfig) -> int:
    with TeamTalkClient.from_config(config) as client:
        client.connect()
        client.login()

        desired_channel = args.channel or config.server.channel or "/"
        channel_password = args.channel_password or config.server.channel_password
        client.join_channel(desired_channel, channel_password)
        client.send_channel_message(args.message)

        print(f"Sent channel message to {client.get_channel_path(client.current_channel_id)}")
    return 0


def _run_send_private(args: argparse.Namespace, config: AppConfig) -> int:
    with TeamTalkClient.from_config(config) as client:
        client.connect()
        client.login()
        resolved_user_id = client.resolve_user_id(args.user)
        client.send_private_message(args.message, resolved_user_id)
        print(f"Sent private message to user #{resolved_user_id}")
    return 0


def _run_send_broadcast(args: argparse.Namespace, config: AppConfig) -> int:
    with TeamTalkClient.from_config(config) as client:
        client.connect()
        client.login()
        client.send_broadcast_message(args.message)
        print("Sent broadcast message")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-config":
        path = write_default_config(path=None if args.config == str(default_config_path()) else Path(args.config))
        print(f"Wrote config to {path}")
        return 0

    try:
        config = _apply_overrides(load_config(Path(args.config)), args)
        if args.command == "login":
            return _run_login(args, config)
        if args.command == "send-channel":
            return _run_send_channel(args, config)
        if args.command == "send-private":
            return _run_send_private(args, config)
        if args.command == "send-broadcast":
            return _run_send_broadcast(args, config)
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except TeamTalkError as exc:
        print(str(exc), file=sys.stderr)
        return 1
