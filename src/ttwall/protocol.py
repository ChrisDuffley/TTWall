from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import queue
import socket
import ssl
import threading
from typing import Protocol

from .config import AppConfig


PROTOCOL_VERSION = "5.14"
CLIENT_VERSION = "ttwall/0.1.0"
MSGTYPE_USER = 1
MSGTYPE_CHANNEL = 2
MSGTYPE_BROADCAST = 3
MAX_TEXT_PAYLOAD_BYTES = 450


class TeamTalkError(RuntimeError):
    """Raised when a TeamTalk command fails."""


@dataclass(slots=True)
class CommandError:
    number: int
    message: str


@dataclass(slots=True)
class CommandResult:
    command_id: int
    ok: bool
    error: CommandError | None = None


@dataclass(slots=True)
class ChannelInfo:
    channel_id: int
    parent_id: int
    name: str
    topic: str = ""

    def path(self, channels: dict[int, "ChannelInfo"]) -> str:
        if self.parent_id == 0:
            return "/"
        parent = channels.get(self.parent_id)
        prefix = "/" if parent is None else parent.path(channels)
        return f"{prefix}{self.name}/" if prefix.endswith("/") else f"{prefix}/{self.name}/"


@dataclass(slots=True)
class UserInfo:
    user_id: int
    username: str
    nickname: str
    channel_id: int = 0


@dataclass(slots=True)
class DeliveredMessage:
    msg_type: int
    from_user_id: int
    to_user_id: int
    channel_id: int
    content: str
    more: bool


class LineTransport(Protocol):
    def readline(self) -> str: ...

    def send_line(self, line: str) -> None: ...

    def close(self) -> None: ...


class SocketLineTransport:
    def __init__(self, host: str, port: int, encrypted: bool, timeout: float) -> None:
        raw_socket = socket.create_connection((host, port), timeout=timeout)
        raw_socket.settimeout(timeout)
        if encrypted:
            context = ssl.create_default_context()
            raw_socket = context.wrap_socket(raw_socket, server_hostname=host)
            raw_socket.settimeout(timeout)
        self._socket = raw_socket
        self._reader = self._socket.makefile("r", encoding="utf-8", newline="")

    def readline(self) -> str:
        line = self._reader.readline()
        if not line:
            raise TeamTalkError("Connection closed by the TeamTalk server")
        return line

    def send_line(self, line: str) -> None:
        self._socket.sendall(line.encode("utf-8"))

    def close(self) -> None:
        try:
            self._reader.close()
        finally:
            self._socket.close()


def escape_command_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def parse_command_line(line: str) -> tuple[str, dict[str, str]]:
    text = line.rstrip("\r\n")
    if not text:
        raise ValueError("Empty TeamTalk command line")

    if " " not in text:
        return text, {}

    command, rest = text.split(" ", 1)
    params: dict[str, str] = {}
    index = 0

    while index < len(rest):
        while index < len(rest) and rest[index].isspace():
            index += 1
        if index >= len(rest):
            break

        key_start = index
        while index < len(rest) and rest[index] not in {"=", " "}:
            index += 1
        key = rest[key_start:index]
        if index >= len(rest) or rest[index] != "=":
            raise ValueError(f"Invalid TeamTalk parameter segment: {rest[key_start:]!r}")
        index += 1

        if index < len(rest) and rest[index] == '"':
            index += 1
            value_chars: list[str] = []
            while index < len(rest):
                char = rest[index]
                if char == "\\":
                    index += 1
                    if index >= len(rest):
                        break
                    escaped = rest[index]
                    if escaped == "n":
                        value_chars.append("\n")
                    elif escaped == "r":
                        value_chars.append("\r")
                    else:
                        value_chars.append(escaped)
                elif char == '"':
                    index += 1
                    break
                else:
                    value_chars.append(char)
                index += 1
            params[key] = "".join(value_chars)
            continue

        value_start = index
        while index < len(rest) and not rest[index].isspace():
            index += 1
        params[key] = rest[value_start:index]

    return command, params


def build_message_chunks(content: str, max_payload_bytes: int = MAX_TEXT_PAYLOAD_BYTES) -> list[tuple[str, bool]]:
    if not content:
        raise ValueError("TeamTalk does not accept empty text messages")

    chunks: list[str] = []
    remaining = content

    while remaining:
        current: list[str] = []
        next_index = 0
        for next_index, char in enumerate(remaining, start=1):
            candidate = "".join(current) + char
            if len(escape_command_value(candidate).encode("utf-8")) > max_payload_bytes:
                break
            current.append(char)
        else:
            next_index = len(remaining) + 1

        if not current:
            raise ValueError("A single character exceeds the TeamTalk payload limit")

        chunk = "".join(current)
        chunks.append(chunk)
        remaining = remaining[len(chunk):]

    return [(chunk, index < len(chunks) - 1) for index, chunk in enumerate(chunks)]


class TeamTalkClient:
    def __init__(
        self,
        host: str,
        tcp_port: int,
        encrypted: bool,
        username: str,
        password: str,
        nickname: str,
        client_name: str,
        timeout: float = 5.0,
        transport: LineTransport | None = None,
    ) -> None:
        self.host = host
        self.tcp_port = tcp_port
        self.encrypted = encrypted
        self.username = username
        self.password = password
        self.nickname = nickname
        self.client_name = client_name
        self.timeout = timeout
        self._transport = transport
        self._next_command_id = 1
        self.server_name = ""
        self.my_user_id = 0
        self.my_user_type = 0
        self.current_channel_id = 0
        self.channels: dict[int, ChannelInfo] = {}
        self.users: dict[int, UserInfo] = {}
        self.messages: list[DeliveredMessage] = []
        self._reader_thread: threading.Thread | None = None
        self._command_queues: dict[int, queue.SimpleQueue] = {}
        self._message_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._reader_active = False

    @classmethod
    def from_config(cls, config: AppConfig) -> "TeamTalkClient":
        return cls(
            host=config.server.host,
            tcp_port=config.server.tcp_port,
            encrypted=config.server.encrypted,
            username=config.identity.username,
            password=config.identity.password,
            nickname=config.identity.nickname,
            client_name=config.identity.client_name,
            timeout=config.defaults.timeout,
        )

    def __enter__(self) -> "TeamTalkClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        if self._transport is None:
            self._transport = SocketLineTransport(self.host, self.tcp_port, self.encrypted, self.timeout)
        command, params = parse_command_line(self._transport.readline())
        self._handle_server_command(command, params)
        if command != "teamtalk":
            raise TeamTalkError(f"Expected TeamTalk welcome command, got {command!r}")

    def close(self) -> None:
        self._reader_active = False
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None

    def login(self) -> None:
        if not self.username:
            raise TeamTalkError("A TeamTalk username is required")
        command_id = self._send_command(
            "login",
            nickname=self.nickname,
            username=self.username,
            password=self.password,
            clientname=self.client_name,
            protocol=PROTOCOL_VERSION,
            version=CLIENT_VERSION,
        )
        result = self._wait_for_command(command_id)
        if not result.ok:
            assert result.error is not None
            raise TeamTalkError(f"Login failed: {result.error.message}")

    def join_channel(self, channel_ref: int | str, password: str = "") -> None:
        channel_id = self.resolve_channel_id(channel_ref)
        if self.current_channel_id == channel_id:
            return
        result = self._wait_for_command(self._send_command("join", chanid=channel_id, password=password))
        if not result.ok:
            assert result.error is not None
            raise TeamTalkError(f"Join failed: {result.error.message}")

    def send_private_message(self, content: str, user_ref: int | str) -> None:
        user_id = self.resolve_user_id(user_ref)
        self._send_text_message(MSGTYPE_USER, content, destuserid=user_id)

    def send_channel_message(self, content: str) -> None:
        if self.current_channel_id == 0:
            raise TeamTalkError("You must join a channel before sending a channel message")
        self._send_text_message(MSGTYPE_CHANNEL, content, chanid=self.current_channel_id)

    def send_broadcast_message(self, content: str) -> None:
        self._send_text_message(MSGTYPE_BROADCAST, content)

    def leave_channel(self) -> None:
        if self.current_channel_id == 0:
            return
        result = self._wait_for_command(self._send_command("leave"))
        if not result.ok:
            assert result.error is not None
            raise TeamTalkError(f"Leave failed: {result.error.message}")

    def start_reader(self) -> None:
        """Start a background thread to continuously read server events (for interactive mode)."""
        self._reader_active = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="ttwall-reader"
        )
        self._reader_thread.start()

    def _reader_loop(self) -> None:
        current_begin_id = 0
        ok_seen = False
        error_seen: CommandError | None = None

        while self._reader_active:
            try:
                line = self._readline()
            except (TeamTalkError, OSError):
                break
            try:
                command, params = parse_command_line(line)
            except ValueError:
                continue

            self._handle_server_command(command, params)

            if command == "begin":
                current_begin_id = self._int_param(params, "id")
                ok_seen = False
                error_seen = None
            elif command == "ok":
                ok_seen = True
            elif command == "error":
                error_seen = CommandError(
                    number=self._int_param(params, "number"),
                    message=params.get("message", "Unknown TeamTalk error"),
                )
            elif command == "end":
                end_id = self._int_param(params, "id")
                if end_id == current_begin_id:
                    q = self._command_queues.pop(end_id, None)
                    if q is not None:
                        q.put(CommandResult(
                            command_id=end_id,
                            ok=ok_seen and error_seen is None,
                            error=error_seen,
                        ))
                    current_begin_id = 0
            elif command == "messagedeliver":
                if self.messages:
                    self._message_queue.put(self.messages[-1])

    def resolve_user_id(self, user_ref: int | str) -> int:
        if isinstance(user_ref, int):
            return user_ref
        candidate = str(user_ref).strip()
        if candidate.isdigit():
            return int(candidate)
        lowered = candidate.casefold()
        for user in self.users.values():
            if user.username.casefold() == lowered or user.nickname.casefold() == lowered:
                return user.user_id
        raise TeamTalkError(f"Unable to resolve user {user_ref!r}")

    def resolve_channel_id(self, channel_ref: int | str) -> int:
        if isinstance(channel_ref, int):
            return channel_ref
        candidate = str(channel_ref).strip()
        if candidate.isdigit():
            return int(candidate)
        normalized = self._normalize_channel_path(candidate)
        for channel in self.channels.values():
            if self._normalize_channel_path(channel.path(self.channels)) == normalized:
                return channel.channel_id
            if channel.name and channel.name.casefold() == candidate.casefold():
                return channel.channel_id
        raise TeamTalkError(f"Unable to resolve channel {channel_ref!r}")

    def get_channel_path(self, channel_id: int) -> str:
        channel = self.channels.get(channel_id)
        if channel is None:
            return "/"
        return channel.path(self.channels)

    def root_channel_id(self) -> int:
        for channel_id, channel in self.channels.items():
            if channel.parent_id == 0:
                return channel_id
        raise TeamTalkError("The TeamTalk server did not publish a root channel")

    def _send_text_message(self, msg_type: int, content: str, **extra_params: int) -> None:
        for chunk, has_more in build_message_chunks(content):
            params: dict[str, object] = {
                "type": msg_type,
                "content": chunk,
                "more": 1 if has_more else 0,
            }
            params.update(extra_params)
            result = self._wait_for_command(self._send_command("message", **params))
            if not result.ok:
                assert result.error is not None
                raise TeamTalkError(f"Message send failed: {result.error.message}")

    def _send_command(self, command: str, **params: object) -> int:
        if self._transport is None:
            raise TeamTalkError("Not connected to a TeamTalk server")

        command_id = self._next_command_id
        self._next_command_id += 1

        if self._reader_active:
            self._command_queues[command_id] = queue.SimpleQueue()

        parts = [command]
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                serialized = "1" if value else "0"
            elif isinstance(value, int):
                serialized = str(value)
            else:
                serialized = f'"{escape_command_value(str(value))}"'
            parts.append(f"{key}={serialized}")
        parts.append(f"id={command_id}")
        self._transport.send_line(" ".join(parts) + "\r\n")
        return command_id

    def _wait_for_command(self, command_id: int) -> CommandResult:
        if self._reader_active:
            q = self._command_queues.get(command_id)
            if q is None:
                raise TeamTalkError(f"No registered queue for command {command_id}")
            try:
                return q.get(timeout=30.0)
            except queue.Empty:
                raise TeamTalkError("Timed out waiting for TeamTalk command response")

        current_reply_id = 0
        success = False
        error: CommandError | None = None

        while True:
            command, params = parse_command_line(self._readline())
            self._handle_server_command(command, params)

            if command == "begin":
                current_reply_id = self._int_param(params, "id")
            elif command == "ok" and current_reply_id == command_id:
                success = True
            elif command == "error" and current_reply_id == command_id:
                error = CommandError(
                    number=self._int_param(params, "number"),
                    message=params.get("message", "Unknown TeamTalk error"),
                )
            elif command == "end" and self._int_param(params, "id") == command_id:
                return CommandResult(command_id=command_id, ok=success and error is None, error=error)

    def _readline(self) -> str:
        if self._transport is None:
            raise TeamTalkError("Not connected to a TeamTalk server")
        return self._transport.readline()

    def _handle_server_command(self, command: str, params: dict[str, str]) -> None:
        if command == "teamtalk":
            self.server_name = params.get("servername", self.server_name)
            self.my_user_id = self._int_param(params, "userid", self.my_user_id)
            return

        if command == "accepted":
            self.my_user_type = self._int_param(params, "usertype", self.my_user_type)
            return

        if command == "serverupdate":
            self.server_name = params.get("servername", self.server_name)
            return

        if command in {"loggedin", "adduser", "updateuser"}:
            user_id = self._int_param(params, "userid")
            self.users[user_id] = UserInfo(
                user_id=user_id,
                username=params.get("username", self.users.get(user_id, UserInfo(user_id, "", "")).username),
                nickname=params.get("nickname", self.users.get(user_id, UserInfo(user_id, "", "")).nickname),
                channel_id=self._int_param(params, "chanid", self.users.get(user_id, UserInfo(user_id, "", "")).channel_id),
            )
            return

        if command == "removeuser":
            user_id = self._int_param(params, "userid")
            if user_id in self.users:
                self.users[user_id].channel_id = 0
            return

        if command == "loggedout":
            self.users.pop(self._int_param(params, "userid"), None)
            return

        if command in {"addchannel", "updatechannel"}:
            channel_id = self._int_param(params, "chanid")
            self.channels[channel_id] = ChannelInfo(
                channel_id=channel_id,
                parent_id=self._int_param(params, "parentid"),
                name=params.get("name", self.channels.get(channel_id, ChannelInfo(channel_id, 0, "")).name),
                topic=params.get("topic", self.channels.get(channel_id, ChannelInfo(channel_id, 0, "")).topic),
            )
            return

        if command == "removechannel":
            self.channels.pop(self._int_param(params, "chanid"), None)
            return

        if command == "joined":
            self.current_channel_id = self._int_param(params, "chanid")
            return

        if command == "left":
            self.current_channel_id = 0
            return

        if command == "messagedeliver":
            self.messages.append(
                DeliveredMessage(
                    msg_type=self._int_param(params, "type"),
                    from_user_id=self._int_param(params, "srcuserid"),
                    to_user_id=self._int_param(params, "destuserid"),
                    channel_id=self._int_param(params, "chanid"),
                    content=params.get("content", ""),
                    more=self._int_param(params, "more") == 1,
                )
            )

    @staticmethod
    def _int_param(params: dict[str, str], key: str, default: int = 0) -> int:
        value = params.get(key)
        if value in {None, ""}:
            return default
        return int(value)

    @staticmethod
    def _normalize_channel_path(path: str) -> str:
        text = path.strip()
        if not text or text == "/":
            return "/"
        stripped = text.strip("/")
        return f"/{stripped}/"
