# TTWall

TTWall is a small Python CLI client for [TeamTalk 5](https://bearware.dk) that logs in over the TeamTalk text protocol and sends channel, private, and broadcast messages.

It uses `configobj` with an INI file so the connection details are easy to edit.

## What It Does

- Logs in to a TeamTalk server over TCP or TLS.
- Joins a channel by path or channel ID when needed.
- Sends private messages by user ID, username, or nickname.
- Sends channel messages directly with TeamTalk's `message type=2` command.
- Sends broadcast messages when the account has broadcast permission.

TTWall does not rely on message interception just to send a channel message. It joins the target channel and sends a normal TeamTalk channel message command.

## Install

Create a virtual environment, install the package, and keep using the generated `ttwall` command.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

For local test dependencies:

```bash
python -m pip install -e .[dev]
```

## Configuration

Generate a starter config file:

```bash
ttwall init-config
```

By default TTWall uses `ttwall.ini` in the current directory when it exists. Otherwise it uses `~/.config/ttwall/ttwall.ini`.

The generated file contains three sections:

```ini
[server]
host =
server_url =
tcp_port = 10333
encrypted = False
channel =
channel_password =

[identity]
nickname = ttwall
username =
password =
client_name = ttwall

[defaults]
timeout = 5.0
prefer_public_ipv4 = True
auto_join = False
```

### Host Resolution

If `server.host` is blank, TTWall behaves as follows:

1. If `server.server_url` is set to a `tt://` or `tls://` URL, TTWall resolves the URL host to an IPv4 address.
2. When multiple IPv4 addresses are available, TTWall prefers a public IPv4 address unless `prefer_public_ipv4` is disabled.
3. If neither `host` nor `server_url` is set, TTWall falls back to `127.0.0.1`.

Example TeamTalk URL:

`tt://tt5us.bearware.dk?tcpport=10335&username=bearware&channel=/`

## Usage

Log in only:

```bash
ttwall login
```

Log in and join a configured or explicit channel:

```bash
ttwall login --join --channel /Ops/
```

Send a channel message. TTWall joins the configured channel, the explicit `--channel`, or `/` when neither is set:

```bash
ttwall send-channel --channel /Ops/ "Deployment starts at 18:00 UTC"
```

Send a private message by user ID, username, or nickname:

```bash
ttwall send-private admin "Please check the server log"
```

Send a broadcast message:

```bash
ttwall send-broadcast "The maintenance window starts now"
```

You can override INI values on the command line, for example:

```bash
ttwall send-private --host 192.168.1.10 --username guest --password guest 42 "hello"
```

## Notes

- Broadcast messages require the TeamTalk user right for broadcast text messages.
- Channel messages require the account to be allowed to join the channel and send channel text.
- Private user lookup uses the users published by the server during login, so usernames and nicknames can be used without a separate lookup command.

## Development

Run the current test suite with:

```bash
. .venv/bin/activate
PYTHONPATH=src python -m pytest
```