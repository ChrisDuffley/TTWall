from __future__ import annotations

from collections import deque

from ttwall.protocol import (
    MSGTYPE_CHANNEL,
    TeamTalkClient,
    build_message_chunks,
    parse_command_line,
)


class FakeTransport:
    def __init__(self, responses: list[str]) -> None:
        self._responses = deque(responses)
        self.sent_lines: list[str] = []
        self.closed = False

    def readline(self) -> str:
        if not self._responses:
            raise AssertionError("The fake transport ran out of protocol lines")
        return self._responses.popleft()

    def send_line(self, line: str) -> None:
        self.sent_lines.append(line)

    def close(self) -> None:
        self.closed = True


def test_parse_command_line_unescapes_strings() -> None:
    command, params = parse_command_line(
        'message type=2 content="hello\\nworld \\\"quoted\\\"" chanid=3 more=0 id=9\r\n'
    )

    assert command == "message"
    assert params["content"] == 'hello\nworld "quoted"'
    assert params["chanid"] == "3"
    assert params["more"] == "0"


def test_build_message_chunks_marks_intermediate_parts() -> None:
    chunks = build_message_chunks("x" * 1000, max_payload_bytes=100)

    assert len(chunks) > 1
    assert chunks[0][1] is True
    assert chunks[-1][1] is False
    assert "".join(chunk for chunk, _ in chunks) == "x" * 1000


def test_client_login_join_and_send_channel_message() -> None:
    transport = FakeTransport(
        [
            'teamtalk userid=42 servername="Example"\r\n',
            'begin id=1\r\n',
            'accepted usertype=1\r\n',
            'serverupdate servername="Example"\r\n',
            'addchannel chanid=1 parentid=0 name="Root"\r\n',
            'addchannel chanid=2 parentid=1 name="Ops"\r\n',
            'loggedin userid=42 nickname="TTWall" username="guest" chanid=0\r\n',
            'loggedin userid=77 nickname="Admin" username="admin" chanid=2\r\n',
            'ok\r\n',
            'end id=1\r\n',
            'begin id=2\r\n',
            'joined chanid=2\r\n',
            'updateuser userid=42 nickname="TTWall" username="guest" chanid=2\r\n',
            'ok\r\n',
            'end id=2\r\n',
            'begin id=3\r\n',
            'ok\r\n',
            'end id=3\r\n',
        ]
    )

    client = TeamTalkClient(
        host="127.0.0.1",
        tcp_port=10333,
        encrypted=False,
        username="guest",
        password="guest",
        nickname="TTWall",
        client_name="ttwall",
        transport=transport,
    )

    client.connect()
    client.login()
    client.join_channel("/Ops/")
    client.send_channel_message("Deployment at 18:00 UTC")

    assert client.server_name == "Example"
    assert client.my_user_id == 42
    assert client.current_channel_id == 2
    assert client.resolve_user_id("admin") == 77
    assert client.get_channel_path(2) == "/Ops/"

    sent_message = transport.sent_lines[-1]
    assert sent_message.startswith("message ")
    command, params = parse_command_line(sent_message)
    assert command == "message"
    assert int(params["type"]) == MSGTYPE_CHANNEL
    assert int(params["chanid"]) == 2
    assert params["content"] == "Deployment at 18:00 UTC"
    assert params["more"] == "0"