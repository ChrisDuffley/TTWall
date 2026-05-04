from pathlib import Path

from ttwall.config import load_config, parse_server_url, write_default_config


def test_parse_server_url_reads_teamtalk_fields() -> None:
    parsed = parse_server_url(
        "tt://tt5us.bearware.dk?tcpport=10335&username=bearware&channel=/&encrypted=1"
    )

    assert parsed["host"] == "tt5us.bearware.dk"
    assert parsed["tcp_port"] == 10335
    assert parsed["username"] == "bearware"
    assert parsed["channel"] == "/"
    assert parsed["encrypted"] is True


def test_load_config_defaults_to_localhost_when_host_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "ttwall.ini"
    write_default_config(config_path)

    config = load_config(config_path)

    assert config.server.host == "127.0.0.1"
    assert config.server.tcp_port == 10333
    assert config.defaults.timeout == 5.0


def test_load_config_uses_server_url_when_host_blank(tmp_path: Path) -> None:
    config_path = tmp_path / "ttwall.ini"
    write_default_config(config_path)
    config_path.write_text(
        "[server]\n"
        "host = \n"
        "server_url = tt://127.0.0.1?tcpport=10443&channel=/ops/&encrypted=1\n"
        "tcp_port = \n"
        "encrypted = False\n"
        "channel = \n"
        "channel_password = \n"
        "\n"
        "[identity]\n"
        "nickname = ttwall\n"
        "username = guest\n"
        "password = guest\n"
        "client_name = ttwall\n"
        "\n"
        "[defaults]\n"
        "timeout = 5.0\n"
        "prefer_public_ipv4 = True\n"
        "auto_join = False\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.server.host == "127.0.0.1"
    assert config.server.tcp_port == 10443
    assert config.server.encrypted is True
    assert config.server.channel == "/ops/"