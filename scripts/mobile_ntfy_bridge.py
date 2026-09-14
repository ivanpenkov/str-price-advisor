#!/usr/bin/env python3
"""
Mobile ntfy Bridge Daemon for Google Antigravity & STR Price Advisor
===================================================================
Enables two-way communication between mobile device and Antigravity:
1. Subscribes to private ntfy.sh inbound command topic via streaming JSON/SSE.
2. Sends instant ACK push notification to phone upon receiving a prompt.
3. Hybrid Smart Router:
   - Deterministic keywords (status, audit, sales, etc.) -> fast CLI execution.
   - Natural language prompts -> headless Antigravity AI Agent (agy --continue).
4. Delivers formatted results back to the user's mobile notification tray.
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("MobileNtfyBridge")


@dataclass
class CommandResult:
    title: str
    output: str
    success: bool
    duration_sec: float
    is_agent: bool
    returncode: int = 0


class MobileBridgeConfig:
    """Loads and encapsulates configuration for mobile bridge."""

    DEFAULT_CONFIG_PATH = Path("config/mobile_bridge.json")

    def __init__(self, config_path: Optional[Path] = None, workspace_dir: Optional[Path] = None):
        self.workspace_dir = workspace_dir or Path(__file__).resolve().parent.parent

        # 1. Load .env if present in workspace root
        env_file = self.workspace_dir / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except Exception as e:
                logger.warning(f"Failed to parse .env file: {e}")

        self.config_file = config_path or (self.workspace_dir / self.DEFAULT_CONFIG_PATH)

        data = {}
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text())
            except Exception as e:
                logger.warning(f"Failed to parse {self.config_file}: {e}")

        # Core topic configuration
        self.inbound_topic: str = os.getenv("NTFY_INBOUND_TOPIC", data.get("inbound_topic", "ivan-str-cmd-8e2b4f91"))
        self.outbound_topic: str = os.getenv("NTFY_OUTBOUND_TOPIC", data.get("outbound_topic", "ivan-str-advisor-xyz"))

        # Executable and tool paths
        self.python_path = Path(data.get("python_path", str(self.workspace_dir / ".venv" / "bin" / "python")))
        if not self.python_path.is_absolute():
            self.python_path = (self.workspace_dir / self.python_path).resolve()

        self.agy_path = Path(data.get("agy_path", "/Users/ivanpe/.local/bin/agy"))
        self.notify_script = Path(data.get("notify_script", str(Path.home() / ".gemini" / "config" / "scripts" / "notify_mobile.sh")))

        # Delays and limits
        self.reconnect_delay: float = float(data.get("reconnect_delay", 3.0))
        self.max_output_length: int = int(data.get("max_output_length", 3800))
        self.command_timeout_sec: int = int(data.get("command_timeout_sec", 300))

        # CLI shortcut table
        self.shortcuts: Dict[str, Dict[str, Any]] = data.get("shortcuts", {})


class CommandRouter:
    """Inspects messages and dispatches to either fast CLI commands or the Antigravity Agent."""

    def __init__(self, config: MobileBridgeConfig):
        self.config = config

    def build_help_menu(self) -> str:
        lines = [
            "📱 Antigravity Mobile Controller — Help Menu",
            "===========================================",
            "Direct CLI Shortcuts (Instant 2–5s Execution):",
        ]
        for name, info in sorted(self.config.shortcuts.items()):
            desc = info.get("desc", "")
            lines.append(f"  • {name:<16} : {desc}")
        lines.append("  • help             : Display this command manual")
        lines.append("")
        lines.append("🧠 Natural Language Agent Tasks:")
        lines.append("  Any other message or voice prompt is automatically routed")
        lines.append("  to the Antigravity AI Agent (agy) with repo context, file")
        lines.append("  editing, testing, and skill invocation.")
        return "\n".join(lines)

    def route_command(self, raw_message: str) -> CommandResult:
        msg = raw_message.strip()
        if not msg:
            return CommandResult(
                title="Empty Message",
                output="No command or prompt provided.",
                success=False,
                duration_sec=0.0,
                is_agent=False,
                returncode=1,
            )

        tokens = msg.split()
        first_word = tokens[0].lower().lstrip("/")
        args = tokens[1:]
        t0 = time.time()

        # 1. Built-in Help
        if first_word in ("help", "--help", "-h"):
            menu = self.build_help_menu()
            return CommandResult(
                title="Command Menu",
                output=menu,
                success=True,
                duration_sec=round(time.time() - t0, 2),
                is_agent=False,
                returncode=0,
            )

        # 2. Registered CLI Shortcuts
        if first_word in self.config.shortcuts:
            shortcut_info = self.config.shortcuts[first_word]
            base_cmd = list(shortcut_info.get("command", []))
            full_cmd = base_cmd + args

            # Resolve python binary path if first element is python or .venv
            if full_cmd and (full_cmd[0] in ("python", ".venv/bin/python", "./.venv/bin/python")):
                full_cmd[0] = str(self.config.python_path)

            logger.info(f"Executing CLI shortcut [{first_word}]: {' '.join(full_cmd)}")
            try:
                proc = subprocess.run(
                    full_cmd,
                    cwd=str(self.config.workspace_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=self.config.command_timeout_sec,
                )
                duration = round(time.time() - t0, 2)
                output = proc.stdout.strip()
                if proc.stderr:
                    output = (output + "\n\nErrors/Warnings:\n" + proc.stderr.strip()).strip()
                if not output:
                    output = f"Command completed with code {proc.returncode} (no output)."

                return CommandResult(
                    title=f"CLI: {first_word}",
                    output=output,
                    success=(proc.returncode == 0),
                    duration_sec=duration,
                    is_agent=False,
                    returncode=proc.returncode,
                )
            except subprocess.TimeoutExpired:
                duration = round(time.time() - t0, 2)
                return CommandResult(
                    title=f"CLI Timeout: {first_word}",
                    output=f"Command timed out after {self.config.command_timeout_sec}s.",
                    success=False,
                    duration_sec=duration,
                    is_agent=False,
                    returncode=-1,
                )
            except Exception as e:
                duration = round(time.time() - t0, 2)
                return CommandResult(
                    title=f"CLI Error: {first_word}",
                    output=f"Execution error: {e}",
                    success=False,
                    duration_sec=duration,
                    is_agent=False,
                    returncode=1,
                )

        # 3. Autonomous Antigravity AI Agent (agy)
        logger.info(f"Routing natural language query to Antigravity Agent: '{msg[:60]}...'")
        if not self.config.agy_path.exists():
            duration = round(time.time() - t0, 2)
            return CommandResult(
                title="Antigravity Agent Unavailable",
                output=f"Could not locate 'agy' binary at {self.config.agy_path}. Run 'agy install' or update config.",
                success=False,
                duration_sec=duration,
                is_agent=True,
                returncode=1,
            )

        cmd = [
            str(self.config.agy_path),
            "-c",
            "-p",
            msg,
            "--dangerously-skip-permissions",
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.config.workspace_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.config.command_timeout_sec,
            )
            duration = round(time.time() - t0, 2)
            output = proc.stdout.strip()
            if proc.returncode != 0 and proc.stderr:
                output = (output + "\n\nErrors/Warnings:\n" + proc.stderr.strip()).strip()
            elif proc.stderr and not output:
                output = proc.stderr.strip()
            if not output:
                output = "Agent completed task (no text response returned)."

            return CommandResult(
                title="Agent Response",
                output=output,
                success=(proc.returncode == 0),
                duration_sec=duration,
                is_agent=True,
                returncode=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            duration = round(time.time() - t0, 2)
            return CommandResult(
                title="Agent Timeout",
                output=f"Agent prompt timed out after {self.config.command_timeout_sec}s.",
                success=False,
                duration_sec=duration,
                is_agent=True,
                returncode=-1,
            )
        except Exception as e:
            duration = round(time.time() - t0, 2)
            return CommandResult(
                title="Agent Error",
                output=f"Agent dispatch failed: {e}",
                success=False,
                duration_sec=duration,
                is_agent=True,
                returncode=1,
            )


class NotificationDispatcher:
    """Sends immediate ACKs and completed result payloads to ntfy."""

    def __init__(self, config: MobileBridgeConfig):
        self.config = config

    def send_notification(
        self,
        title: str,
        message: str,
        tags: str = "white_check_mark,rocket",
        priority: str = "default",
    ) -> bool:
        """Dispatches notification using notify_mobile.sh or falls back to requests.post."""
        if self.config.notify_script.exists():
            try:
                cmd = [
                    str(self.config.notify_script),
                    "--topic",
                    self.config.outbound_topic,
                    "--title",
                    title,
                    "--message",
                    message,
                    "--tags",
                    tags,
                    "--priority",
                    priority,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    logger.info(f"✓ Push notification sent via notify_mobile.sh: '{title}'")
                    return True
                else:
                    logger.warning(f"notify_mobile.sh failed (code {proc.returncode}): {proc.stderr.strip()}")
            except Exception as e:
                logger.warning(f"notify_mobile.sh invocation failed: {e}")

        # Fallback to direct HTTP POST
        try:
            url = f"https://ntfy.sh/{self.config.outbound_topic}"
            headers = {
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            }
            resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
            if resp.status_code == 200:
                logger.info(f"✓ Push notification sent via direct HTTP POST: '{title}'")
                return True
            logger.warning(f"Direct ntfy HTTP POST returned {resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"Direct ntfy notification POST failed: {e}")
            return False

    def send_ack(self, raw_message: str) -> bool:
        preview = raw_message.strip()
        if len(preview) > 75:
            preview = preview[:72] + "..."
        return self.send_notification(
            title="Antigravity: Command Received",
            message=f'⏳ Processing: "{preview}"',
            tags="hourglass_flowing_sand,gear",
            priority="default",
        )

    def send_response(self, result: CommandResult) -> bool:
        # Safe length truncation
        output = result.output
        if len(output) > self.config.max_output_length:
            truncate_point = max(0, self.config.max_output_length - 60)
            output = output[:truncate_point] + "\n\n... [Output truncated to 3,800 characters]"

        # Format title and tags
        if result.success:
            title = f"Antigravity: {result.title} ({result.duration_sec}s)"
            tags = "white_check_mark,rocket"
            priority = "default"
        else:
            title = f"Antigravity Error: {result.title} ({result.duration_sec}s)"
            tags = "warning,x"
            priority = "high"

        return self.send_notification(
            title=title,
            message=output,
            tags=tags,
            priority=priority,
        )


class NtfyBridgeDaemon:
    """Subscribes to ntfy inbound topic stream and manages the lifecycle."""

    def __init__(self, config: Optional[MobileBridgeConfig] = None):
        self.config = config or MobileBridgeConfig()
        self.router = CommandRouter(self.config)
        self.dispatcher = NotificationDispatcher(self.config)
        self.seen_ids: Set[str] = set()
        self.startup_time: float = time.time()
        self.running: bool = False

    def handle_message_payload(self, data: Dict[str, Any]) -> Optional[CommandResult]:
        event_type = data.get("event")
        if event_type != "message":
            return None

        msg_id = data.get("id")
        if not msg_id or msg_id in self.seen_ids:
            return None
        self.seen_ids.add(msg_id)

        # Ignore messages sent prior to daemon launch (buffer window: 5s)
        msg_time = float(data.get("time", 0))
        if msg_time and msg_time < (self.startup_time - 5.0):
            logger.debug(f"Ignoring historical message {msg_id} from {msg_time}")
            return None

        raw_text = data.get("message", "").strip()
        if not raw_text:
            return None

        logger.info(f"Incoming Mobile Prompt [{msg_id}]: {raw_text}")

        # 1. Send Immediate ACK push to phone
        self.dispatcher.send_ack(raw_text)

        # 2. Route and execute command
        result = self.router.route_command(raw_text)

        # 3. Deliver final completed result
        self.dispatcher.send_response(result)
        return result

    def run_stream(self, max_messages: Optional[int] = None):
        """Streams inbound events from ntfy.sh JSON endpoint."""
        self.running = True
        self.startup_time = time.time()
        url = f"https://ntfy.sh/{self.config.inbound_topic}/json"
        processed_count = 0

        logger.info("=======================================================")
        logger.info("📱 Antigravity Mobile ntfy Bridge Daemon Active")
        logger.info(f"   Inbound Command Topic:  https://ntfy.sh/{self.config.inbound_topic}")
        logger.info(f"   Outbound Alert Topic:   https://ntfy.sh/{self.config.outbound_topic}")
        logger.info(f"   Workspace:              {self.config.workspace_dir}")
        logger.info(f"   Agent Executable:       {self.config.agy_path}")
        logger.info("=======================================================")

        current_delay = self.config.reconnect_delay
        while self.running:
            try:
                logger.info(f"Connecting to stream: {url}")
                with requests.get(url, stream=True, timeout=60) as resp:
                    if resp.status_code != 200:
                        logger.warning(f"HTTP {resp.status_code} from ntfy stream. Reconnecting in {current_delay:.1f}s...")
                        if current_delay > 0:
                            time.sleep(current_delay)
                        current_delay = min(current_delay * 1.5, 30.0) if current_delay > 0 else 0.0
                        continue

                    current_delay = self.config.reconnect_delay
                    logger.info("✓ Connected to ntfy command stream. Listening for mobile requests...")
                    for line in resp.iter_lines():
                        if not self.running:
                            break
                        if not line:
                            continue
                        try:
                            payload = json.loads(line.decode("utf-8"))
                        except Exception as e:
                            logger.warning(f"Malformed JSON in stream: {e}")
                            continue

                        res = self.handle_message_payload(payload)
                        if res is not None:
                            processed_count += 1
                            if max_messages and processed_count >= max_messages:
                                logger.info(f"Reached max message limit ({max_messages}). Stopping stream.")
                                self.running = False
                                break

            except (requests.exceptions.RequestException, TimeoutError) as e:
                if self.running:
                    logger.warning(f"Stream disconnected ({e}). Reconnecting in {current_delay:.1f}s...")
                    if current_delay > 0:
                        time.sleep(current_delay)
                    current_delay = min(current_delay * 1.5, 30.0) if current_delay > 0 else 0.0
            except Exception as e:
                logger.error(f"Unexpected error in stream loop: {e}", exc_info=True)
                if self.running and current_delay > 0:
                    time.sleep(current_delay)
                current_delay = min(current_delay * 1.5, 30.0) if current_delay > 0 else 0.0

    def stop(self):
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="Antigravity Mobile ntfy Bridge Daemon")
    parser.add_argument("--test-prompt", type=str, default=None, help="Execute a single test prompt and exit")
    parser.add_argument("--inbound-topic", type=str, default=None, help="Override inbound ntfy command topic")
    parser.add_argument("--outbound-topic", type=str, default=None, help="Override outbound ntfy alert topic")
    parser.add_argument("--max-messages", type=int, default=None, help="Stop after processing N messages (testing)")
    args = parser.parse_args()

    config = MobileBridgeConfig()
    if args.inbound_topic:
        config.inbound_topic = args.inbound_topic
    if args.outbound_topic:
        config.outbound_topic = args.outbound_topic

    daemon = NtfyBridgeDaemon(config)

    def handle_signal(sig, frame):
        logger.info(f"Caught signal {sig}, stopping mobile bridge daemon...")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if args.test_prompt:
        logger.info(f"Running standalone test prompt: '{args.test_prompt}'")
        res = daemon.router.route_command(args.test_prompt)
        print(f"\n--- [{res.title}] (Success: {res.success}, Duration: {res.duration_sec}s) ---")
        print(res.output)
        daemon.dispatcher.send_response(res)
        return

    daemon.run_stream(max_messages=args.max_messages)


if __name__ == "__main__":
    main()
