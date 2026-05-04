from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from socket import AF_INET, getaddrinfo
from typing import Any
from urllib.parse import parse_qs, urlparse

from configobj import ConfigObj


DEFAULT_CONFIG = {
    "server": {
        "host": "",
        "server_url": "",
        "tcp_port": "10333",
        "encrypted": "False",
        "channel": "",
        "channel_password": "",
    },
    "identity": {
        "nickname": "ttwall",
        "username": "",
        "password": "",
        "client_name": "ttwall",
    },
    "defaults": {
        "timeout": "5.0",
        "prefer_public_ipv4": "True",
        "auto_join": "False",
    },
}


@dataclass(slots=True)
class ServerConfig:
    host: str
    tcp_port: int
    encrypted: bool
    channel: str
    channel_password: str


@dataclass(slots=True)
class IdentityConfig:
    nickname: str
    username: str
    password: str
    client_name: str


@dataclass(slots=True)
class DefaultsConfig:
    timeout: float
    prefer_public_ipv4: bool
    auto_join: bool


@dataclass(slots=True)
class AppConfig:
    server: ServerConfig
    identity: IdentityConfig
    defaults: DefaultsConfig
    path: Path


def default_config_path() -> Path:
    cwd_path = Path.cwd() / "ttwall.ini"
    if cwd_path.exists():
        return cwd_path
    return Path.home() / ".config" / "ttwall" / "ttwall.ini"


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_server_url(server_url: str) -> dict[str, Any]:
    if not server_url:
        return {}

    parsed = urlparse(server_url)
    if not parsed.scheme:
        raise ValueError("Server URL must include a scheme such as tt:// or tls://")

    query = parse_qs(parsed.query)
    encrypted = parsed.scheme.lower() in {"tls", "tts", "tts+tcp"}
    if "encrypted" in query:
        encrypted = query["encrypted"][0] in {"1", "true", "True", "yes"}

    result: dict[str, Any] = {
        "host": parsed.hostname or "",
        "tcp_port": int(query.get("tcpport", [parsed.port or 10333])[0]),
        "encrypted": encrypted,
    }

    if "channel" in query:
        result["channel"] = query["channel"][0]
    if "chanpasswd" in query:
        result["channel_password"] = query["chanpasswd"][0]
    if "username" in query:
        result["username"] = query["username"][0]

    return result


def choose_ipv4_address(hostname: str, prefer_public: bool = True) -> str:
    try:
        ip_address(hostname)
        return hostname
    except ValueError:
        pass

    infos = getaddrinfo(hostname, None, AF_INET)
    candidates: list[str] = []
    for _, _, _, _, sockaddr in infos:
        addr = sockaddr[0]
        if addr not in candidates:
            candidates.append(addr)

    if not candidates:
        raise ValueError(f"Unable to resolve an IPv4 address for {hostname!r}")

    if prefer_public:
        for addr in candidates:
            parsed = ip_address(addr)
            if not (parsed.is_private or parsed.is_loopback or parsed.is_link_local):
                return addr

    return candidates[0]


def _merge_defaults(config_obj: ConfigObj) -> None:
    for section_name, values in DEFAULT_CONFIG.items():
        section = config_obj.setdefault(section_name, {})
        for key, value in values.items():
            section.setdefault(key, value)


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    ensure_parent_dir(config_path)

    if config_path.exists():
        config_obj = ConfigObj(str(config_path), encoding="utf-8")
    else:
        config_obj = ConfigObj(encoding="utf-8")

    _merge_defaults(config_obj)

    server = config_obj["server"]
    identity = config_obj["identity"]
    defaults = config_obj["defaults"]
    server_url_values = parse_server_url(server.get("server_url", ""))

    host_value = server.get("host", "").strip() or server_url_values.get("host", "")
    if host_value:
        resolved_host = choose_ipv4_address(
            host_value,
            prefer_public=_bool_value(defaults.get("prefer_public_ipv4", "True")),
        )
    else:
        resolved_host = "127.0.0.1"

    resolved_tcp_port = int(server.get("tcp_port", "10333") or server_url_values.get("tcp_port", 10333))
    if server_url_values.get("tcp_port") and str(server.get("tcp_port", "")).strip() == "":
        resolved_tcp_port = int(server_url_values["tcp_port"])

    encrypted = _bool_value(server.get("encrypted", "False"))
    if not server.get("host", "").strip() and server_url_values.get("encrypted") is not None:
        encrypted = bool(server_url_values["encrypted"])

    channel = server.get("channel", "").strip() or str(server_url_values.get("channel", ""))
    channel_password = server.get("channel_password", "").strip() or str(server_url_values.get("channel_password", ""))
    username = identity.get("username", "").strip() or str(server_url_values.get("username", ""))

    return AppConfig(
        server=ServerConfig(
            host=resolved_host,
            tcp_port=resolved_tcp_port,
            encrypted=encrypted,
            channel=channel,
            channel_password=channel_password,
        ),
        identity=IdentityConfig(
            nickname=identity.get("nickname", "ttwall").strip() or "ttwall",
            username=username,
            password=identity.get("password", ""),
            client_name=identity.get("client_name", "ttwall").strip() or "ttwall",
        ),
        defaults=DefaultsConfig(
            timeout=float(defaults.get("timeout", "5.0")),
            prefer_public_ipv4=_bool_value(defaults.get("prefer_public_ipv4", "True")),
            auto_join=_bool_value(defaults.get("auto_join", "False")),
        ),
        path=config_path,
    )


def write_default_config(path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    ensure_parent_dir(config_path)
    config_obj = ConfigObj(encoding="utf-8")
    for section_name, values in DEFAULT_CONFIG.items():
        config_obj[section_name] = dict(values)
    config_obj.filename = str(config_path)
    config_obj.initial_comment = [
        "TTWall configuration",
        "Leave server.host blank to fall back to the local address (127.0.0.1) or",
        "set server.server_url to a tt:// or tls:// URL and TTWall will resolve an IPv4 address.",
    ]
    config_obj.write()
    return config_path
