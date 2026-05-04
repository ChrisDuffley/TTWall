from __future__ import annotations

import argparse
import queue
import sys
import threading
from pathlib import Path

from .config import AppConfig, default_config_path, load_config, write_default_config
from .protocol import (
    MSGTYPE_BROADCAST,
    MSGTYPE_CHANNEL,
    MSGTYPE_USER,
    DeliveredMessage,
    TeamTalkClient,
    TeamTalkError,
)


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

    interactive_parser = subparsers.add_parser("interactive", help="Start an interactive TeamTalk shell")
    _add_common_options(interactive_parser)
    interactive_parser.add_argument("--channel", help="Channel to join on startup (name, path, or ID)")
    interactive_parser.add_argument("--channel-password", default="", help="Password for the startup channel")

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


# ---------------------------------------------------------------------------
# Interactive shell helpers
# ---------------------------------------------------------------------------

def _format_message(msg: DeliveredMessage, client: TeamTalkClient) -> str:
    sender = client.users.get(msg.from_user_id)
    sender_name = (sender.nickname or sender.username) if sender else f"#{msg.from_user_id}"
    if msg.msg_type == MSGTYPE_CHANNEL:
        return f"[{client.get_channel_path(msg.channel_id)}] {sender_name}: {msg.content}"
    if msg.msg_type == MSGTYPE_USER:
        return f"[PM from {sender_name}] {msg.content}"
    if msg.msg_type == MSGTYPE_BROADCAST:
        return f"[Broadcast] {sender_name}: {msg.content}"
    return f"[?] {sender_name}: {msg.content}"


def _print_interactive_help() -> None:
    print(
        "\n  Commands:\n"
        "    msg <text>              Send a message to the current channel\n"
        "    private <user> <text>   Send a private message\n"
        "    broadcast <text>        Send a broadcast message to all users\n"
        "    join <channel> [pass]   Join a channel (name, path, or ID)\n"
        "    leave                   Leave the current channel\n"
        "    users                   List connected users\n"
        "    channels                List known channels\n"
        "    status                  Show your current connection status\n"
        "    help                    Show this help\n"
        "    quit                    Disconnect and exit\n"
        "\n  Shortcuts: c/say=msg  p/pm=private  bc=broadcast  u=users  ch=channels\n"
    )


def _print_users(client: TeamTalkClient) -> None:
    if not client.users:
        print("  No users visible.")
        return
    for u in sorted(client.users.values(), key=lambda x: x.user_id):
        chan = client.get_channel_path(u.channel_id) if u.channel_id else "(no channel)"
        print(f"  #{u.user_id:<5}  {(u.nickname or u.username):<30}  {chan}")


def _print_channels(client: TeamTalkClient) -> None:
    if not client.channels:
        print("  No channels visible.")
        return
    for ch in sorted(client.channels.values(), key=lambda x: x.channel_id):
        current = " *" if ch.channel_id == client.current_channel_id else ""
        print(f"  #{ch.channel_id:<4}  {ch.path(client.channels)}{current}")


def _run_interactive(args: argparse.Namespace, config: AppConfig) -> int:
    try:
        import readline as _rl  # noqa: F401 - enables line history/editing on Linux/macOS
    except ImportError:
        pass

    stop_display = threading.Event()

    with TeamTalkClient.from_config(config) as client:
        client.connect()
        client.login()
        print(
            f"[Connected] server={client.server_name or config.server.host}"
            f"  you=#{client.my_user_id} ({config.identity.nickname})"
        )

        channel_ref = getattr(args, "channel", None) or config.server.channel
        if channel_ref:
            channel_password = getattr(args, "channel_password", "") or config.server.channel_password
            client.join_channel(channel_ref, channel_password)
            print(f"[Joined] {client.get_channel_path(client.current_channel_id)}")

        client.start_reader()

        def _display_worker() -> None:
            while not stop_display.is_set():
                try:
                    msg = client._message_queue.get(timeout=0.2)
                    sys.stdout.write(f"\n{_format_message(msg, client)}\n")
                    sys.stdout.flush()
                except queue.Empty:
                    pass

        display_thread = threading.Thread(target=_display_worker, daemon=True, name="ttwall-display")
        display_thread.start()

        print("Type 'help' for commands, 'quit' to exit.\n")
        try:
            while True:
                try:
                    raw = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                if not raw:
                    continue

                tokens = raw.split(None, 1)
                cmd = tokens[0].lower()
                args_str = tokens[1].strip() if len(tokens) > 1 else ""

                try:
                    if cmd in ("quit", "exit", "q"):
                        break
                    elif cmd == "help":
                        _print_interactive_help()
                    elif cmd in ("status", "me", "whoami"):
                        chan = (
                            client.get_channel_path(client.current_channel_id)
                            if client.current_channel_id
                            else "(none)"
                        )
                        print(f"  You are #{client.my_user_id} ({config.identity.nickname})  channel: {chan}")
                    elif cmd in ("users", "u"):
                        _print_users(client)
                    elif cmd in ("channels", "ch", "chans"):
                        _print_channels(client)
                    elif cmd == "join":
                        parts = args_str.split(None, 1)
                        if not parts:
                            print("  Usage: join <channel> [password]")
                        else:
                            client.join_channel(parts[0], parts[1] if len(parts) > 1 else "")
                            print(f"[Joined] {client.get_channel_path(client.current_channel_id)}")
                    elif cmd == "leave":
                        client.leave_channel()
                        print("[Left channel]")
                    elif cmd in ("msg", "say", "c"):
                        if not args_str:
                            print("  Usage: msg <text>")
                        else:
                            client.send_channel_message(args_str)
                    elif cmd in ("private", "pm", "p"):
                        parts = args_str.split(None, 1)
                        if len(parts) < 2:
                            print("  Usage: private <user> <text>")
                        else:
                            client.send_private_message(parts[1], parts[0])
                    elif cmd in ("broadcast", "bc"):
                        if not args_str:
                            print("  Usage: broadcast <text>")
                        else:
                            client.send_broadcast_message(args_str)
                    else:
                        print(f"  Unknown command: {cmd!r}  (type 'help' for available commands)")
                except TeamTalkError as exc:
                    print(f"  Error: {exc}")
        finally:
            stop_display.set()

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
        if args.command == "interactive":
            return _run_interactive(args, config)
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except TeamTalkError as exc:
        print(str(exc), file=sys.stderr)
        return 1
