#!/usr/bin/env python3
"""
Telegram Client Module
Pure telegram messaging functionality with source attribution.

stdlib-only (urllib), not `requests` - so this can run inside the shared
vbpub test-gate image (tester-unified), which does not carry `requests` in
its dependency closure. Converted from a prior requests-based
implementation; public method signatures and return-value contracts
(True/False/int/None) are unchanged, so every existing caller
(benchmark.py, sysinfo-notify.py, ssh-keygen-deploy.py's subprocess
invocation) needs no changes beyond the new import path.
"""

import json
import mimetypes
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def _encode_multipart(fields, files):
    """fields: {name: str}; files: {name: (filename, bytes)}. Returns (body_bytes, content_type_header)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    for name, (filename, content) in files.items():
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        header = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        parts.append(header + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _request(method, url, *, data=None, files=None, timeout=10):
    """POST/GET and return (status_code, parsed_json_or_{}, raw_body_text).

    Raises ConnectionError/TimeoutError (stdlib built-ins) for network-level
    failures - the same two names callers already branch on for retry logic.
    A non-2xx HTTP response is NOT an exception here (matches
    requests.post(...) before .raise_for_status() is called) - callers
    decide per-method whether/how to retry on it, same as the original.
    """
    body = None
    headers = {}
    if files:
        body, content_type = _encode_multipart(data or {}, files)
        headers["Content-Type"] = content_type
    elif data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            return resp.status, parsed, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except ValueError:
            parsed = {}
        return e.code, parsed, raw
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TimeoutError(str(e.reason)) from e
        raise ConnectionError(str(e.reason)) from e
    except TimeoutError:
        raise


class TelegramClient:
    """Telegram messaging client with automatic source attribution"""

    def __init__(self, bot_token=None, chat_id=None, thread_id=None):
        self.bot_token = bot_token or os.environ.get('TELEGRAM_BOT_TOKEN')
        self.chat_id = chat_id or os.environ.get('TELEGRAM_CHAT_ID')
        # Optional: post into a forum topic thread (supergroups with topics enabled).
        # Bot API param: message_thread_id
        self.thread_id = (
            thread_id
            if thread_id is not None
            else (os.environ.get('TELEGRAM_THREAD_ID') or os.environ.get('TELEGRAM_MESSAGE_THREAD_ID'))
        )

        if not self.bot_token or not self.chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.system_id = self._get_system_id()

    def create_forum_topic(self, name, icon_color=None, icon_custom_emoji_id=None, max_retries=3, retry_delay=2):
        """Create a forum topic in a supergroup that has Topics enabled.

        Returns:
            int | None: message_thread_id if successful, else None
        """
        url = f"{self.api_url}/createForumTopic"
        data = {
            'chat_id': self.chat_id,
            'name': name,
        }
        if icon_color is not None:
            data['icon_color'] = icon_color
        if icon_custom_emoji_id is not None:
            data['icon_custom_emoji_id'] = icon_custom_emoji_id

        last_error = None
        for attempt in range(max_retries):
            try:
                status, payload, _raw = _request("POST", url, data=data, timeout=10)
                if not (200 <= status < 300):
                    last_error = payload
                    return None
                if not payload.get('ok'):
                    last_error = payload
                    return None
                result = payload.get('result', {}) or {}
                thread_id = result.get('message_thread_id')
                if thread_id is None:
                    return None
                try:
                    return int(thread_id)
                except Exception:
                    return None
            except ConnectionError as e:
                last_error = f"Connection error: {e}"
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
            except TimeoutError as e:
                last_error = f"Timeout error: {e}"
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue

        print(f"Failed to create forum topic after {max_retries} attempts: {last_error}")
        return None

    def _get_system_id(self):
        """Get system identification (FQDN + IP)"""
        try:
            hostname = socket.getfqdn()
            if hostname in ('localhost', 'localhost.localdomain', ''):
                hostname = socket.gethostname()
        except Exception:
            hostname = socket.gethostname()

        try:
            result = subprocess.run(
                ['hostname', '-I'],
                capture_output=True,
                text=True,
                timeout=5
            )
            ip = result.stdout.strip().split()[0] if result.stdout.strip() else "unknown"
        except Exception:
            ip = "unknown"

        return f"{hostname} ({ip})"

    def send_message(self, text, parse_mode='HTML', prefix_source=True, max_retries=3, retry_delay=2, message_thread_id=None):
        """
        Send text message with automatic source attribution and retry logic

        Returns:
            bool: True if successful, False otherwise
        """
        thread_id = message_thread_id if message_thread_id is not None else self.thread_id
        if prefix_source and thread_id not in (None, ""):
            prefix_source = False

        prefixed_text = f"<b>{self.system_id}</b>\n{text}" if prefix_source else text

        url = f"{self.api_url}/sendMessage"
        data = {
            'chat_id': self.chat_id,
            'text': prefixed_text,
            'parse_mode': parse_mode
        }
        if thread_id not in (None, ""):
            data['message_thread_id'] = thread_id

        last_error = None
        for attempt in range(max_retries):
            try:
                status, payload, raw = _request("POST", url, data=data, timeout=10)
                if 200 <= status < 300:
                    return True
                last_error = f"Request error: HTTP {status}: {raw[:300]}"
                print(f"Error sending message: {last_error}")
                return False
            except ConnectionError as e:
                last_error = f"Connection error: {e}"
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Connection error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
            except TimeoutError as e:
                last_error = f"Timeout error: {e}"
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Timeout, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue

        print(f"Failed to send message after {max_retries} attempts: {last_error}")
        return False

    def send_document(self, file_path, caption=None, prefix_source=True, max_retries=3, retry_delay=2, message_thread_id=None):
        """
        Send document file with retry logic

        Returns:
            bool: True if successful, False otherwise
        """
        if not os.path.exists(file_path):
            print(f"Error: File not found: {file_path}")
            return False

        thread_id = message_thread_id if message_thread_id is not None else self.thread_id
        if prefix_source and thread_id not in (None, ""):
            prefix_source = False

        if caption and prefix_source:
            caption = f"{self.system_id}\n{caption}"

        url = f"{self.api_url}/sendDocument"

        last_error = None
        for attempt in range(max_retries):
            try:
                with open(file_path, 'rb') as f:
                    content = f.read()
                data = {'chat_id': self.chat_id}
                if thread_id not in (None, ""):
                    data['message_thread_id'] = thread_id
                if caption:
                    data['caption'] = caption
                    data['parse_mode'] = 'HTML'

                status, payload, raw = _request(
                    "POST", url, data=data,
                    files={'document': (os.path.basename(file_path), content)}, timeout=30,
                )
                if 200 <= status < 300:
                    return True
                last_error = f"HTTP {status}: {raw[:300]}"
                print(f"Error sending document: {last_error}")
                return False
            except ConnectionError as e:
                last_error = f"Connection error: {e}"
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Connection error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
            except TimeoutError as e:
                last_error = f"Timeout error: {e}"
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Timeout, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                last_error = f"Error: {e}"
                print(f"Error sending document: {e}")
                return False

        print(f"Failed to send document after {max_retries} attempts: {last_error}")
        return False

    def send_media_group(self, file_paths, caption=None, prefix_source=True, max_retries=3, retry_delay=2, message_thread_id=None):
        """
        Send multiple images/files as a media group (album) in a single message

        Returns:
            bool: True if successful, False otherwise
        """
        if not file_paths:
            print("Error: No files provided")
            return False

        valid_files = [f for f in file_paths if os.path.exists(f)]
        if not valid_files:
            print("Error: None of the provided files exist")
            return False

        if len(valid_files) < len(file_paths):
            missing = set(file_paths) - set(valid_files)
            print(f"Warning: Skipping missing files: {missing}")

        thread_id = message_thread_id if message_thread_id is not None else self.thread_id
        if prefix_source and thread_id not in (None, ""):
            prefix_source = False

        if caption and prefix_source:
            caption = f"{self.system_id}\n{caption}"

        url = f"{self.api_url}/sendMediaGroup"

        last_error = None
        for attempt in range(max_retries):
            media = []
            files = {}
            for idx, file_path in enumerate(valid_files):
                attach_name = f"file{idx}"
                media_item = {'type': 'document', 'media': f'attach://{attach_name}'}
                if idx == 0 and caption:
                    media_item['caption'] = caption
                    media_item['parse_mode'] = 'HTML'
                media.append(media_item)
                with open(file_path, 'rb') as f:
                    files[attach_name] = (os.path.basename(file_path), f.read())

            data = {'chat_id': self.chat_id, 'media': json.dumps(media)}
            if thread_id not in (None, ""):
                data['message_thread_id'] = thread_id

            try:
                status, payload, raw = _request("POST", url, data=data, files=files, timeout=60)

                if status != 200:
                    print(f"Telegram API error (status {status}): {payload}")
                    if status == 400:
                        error_desc = payload.get('description', '')
                        if 'group send failed' in error_desc.lower():
                            print("Hint: Media group might be too large or contain too many items")
                            print(f"     Attempted to send {len(valid_files)} files")
                        elif 'wrong file identifier' in error_desc.lower():
                            print("Hint: File format might not be supported in media groups")
                    if status == 429 or 'too many requests' in str(payload.get('description', '')).lower():
                        retry_after = payload.get('parameters', {}).get('retry_after', 60)
                        print("Hint: Rate limited by Telegram. Reduce sending frequency")
                        print(f"     Retry after {retry_after} seconds")
                        if attempt < max_retries - 1:
                            time.sleep(retry_after)
                            continue
                    if 400 <= status < 500:
                        return False  # client errors are not retried
                    last_error = f"HTTP {status}: {payload.get('description', raw[:300])}"
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        print(f"Server error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    return False

                return True

            except ConnectionError as e:
                last_error = f"Connection error: {e}"
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Connection error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
            except TimeoutError as e:
                last_error = f"Timeout error: {e}"
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Timeout, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                last_error = f"Error: {e}"
                print(f"Error sending media group: {e}")
                return False

        print(f"Failed to send media group after {max_retries} attempts: {last_error}")
        return False

    def test_connection(self):
        """Test bot connection"""
        url = f"{self.api_url}/getMe"
        try:
            status, data, _raw = _request("GET", url, timeout=10)
            if status == 200 and data.get('ok'):
                bot_info = data.get('result', {})
                print(f"✓ Bot connected: @{bot_info.get('username')}")
                print(f"  Bot name: {bot_info.get('first_name')}")
                print(f"  Source ID: {self.system_id}")
                return True
            print(f"✗ Bot connection failed: {data}")
            return False
        except (ConnectionError, TimeoutError) as e:
            print(f"✗ Connection error: {e}")
            return False


def main():
    """CLI interface for testing"""
    import argparse

    parser = argparse.ArgumentParser(description='Telegram Client')
    parser.add_argument('--test', action='store_true', help='Test connection')
    parser.add_argument('--send', metavar='TEXT', help='Send message')
    parser.add_argument('--file', metavar='PATH', help='Send file')
    parser.add_argument('--caption', metavar='TEXT', help='Caption for file (used with --file)')
    parser.add_argument('--thread-id', metavar='ID', help='Optional: Telegram forum topic message_thread_id (or set TELEGRAM_THREAD_ID)')
    parser.add_argument('--create-topic', metavar='TITLE', help='Create a Telegram forum topic (supergroup with topics enabled) and print message_thread_id')
    parser.add_argument('--bot-token', help='Bot token (or use TELEGRAM_BOT_TOKEN env)')
    parser.add_argument('--chat-id', help='Chat ID (or use TELEGRAM_CHAT_ID env)')

    args = parser.parse_args()

    try:
        client = TelegramClient(args.bot_token, args.chat_id, thread_id=args.thread_id)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    if args.create_topic:
        thread_id = client.create_forum_topic(args.create_topic)
        if thread_id is None:
            print("")
            return 1
        print(str(thread_id))
        return 0

    if args.test:
        return 0 if client.test_connection() else 1

    if args.send:
        success = client.send_message(args.send, message_thread_id=args.thread_id)
        print("✓ Message sent" if success else "✗ Failed to send message")
        return 0 if success else 1

    if args.file:
        success = client.send_document(args.file, caption=args.caption, message_thread_id=args.thread_id)
        print("✓ File sent" if success else "✗ Failed to send file")
        return 0 if success else 1

    parser.print_help()
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
