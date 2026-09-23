"""
Remote System Run Monitoring & Diagnostics - RunTracker Lifecycle Manager.

Instruments scheduled daemons and manual CLI executions:
1. Captures lifecycle metadata (timestamps, duration, status, exit codes).
2. Multiplexes stdout/stderr streams to console and in-memory buffer.
3. Scrubs secrets from environment variables and regex patterns before saving logs.
4. Atomically updates run_history.json and copies to docs/data/run_history.json.
5. Prunes log artifacts older than 14 days and run records older than 30 days.
6. Executes automated surgical git commit & push on failure.
"""

from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import traceback
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parent.parent

JOB_TITLES: Dict[str, str] = {
    "pms-sync": "Daily Streamline PMS Sync",
    "daily-quickscan": "Daily 90-Day Quick Market Scan",
    "weekly-fullscan": "Weekly Full 12-Month Market Scan",
    "manual-cli": "Manual CLI Execution",
}

SCRUB_EXCLUSIONS: Set[str] = {
    "true",
    "false",
    "none",
    "null",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "production",
    "development",
    "staging",
    "seattle",
    "washington",
    "phoenix",
    "arizona",
    "villasol",
    "kivoya",
    "streamline",
    "standard",
}


def get_active_host() -> str:
    """Return configured or system host identifier."""
    configured = os.getenv("STR_HOST_NAME")
    if configured and configured.strip():
        return configured.strip()
    try:
        raw_name = socket.gethostname()
        return raw_name.split(".")[0] if raw_name else "mac-mini"
    except Exception:
        return "mac-mini"


def format_duration(seconds: Optional[float]) -> Optional[str]:
    """Format duration in seconds to human-readable string like '44s', '6m 37s', or '1h 12m 30s'."""
    if seconds is None:
        return None
    sec = max(0, int(round(seconds)))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


class SecretScrubber:
    """
    Scans environment variables and detects structural secret patterns to redact
    sensitive credentials, API keys, passwords, and tokens from log output.
    """

    def __init__(self, env_path: Optional[Path] = None):
        self.secrets: Set[str] = set()
        self.env_path = env_path or (REPO_ROOT / ".env")
        self._load_secrets()

    def _load_secrets(self) -> None:
        sensitive_key_rx = re.compile(
            r"(KEY|SECRET|TOKEN|PASS|PASSWORD|AUTH|CREDENTIAL|BEARER|SOCKS|PRIVATE)",
            re.IGNORECASE,
        )

        def check_and_add(val: Any) -> None:
            if not isinstance(val, str):
                return
            cleaned = val.strip().strip("'\"")
            if len(cleaned) >= 8 and cleaned.lower() not in SCRUB_EXCLUSIONS:
                self.secrets.add(cleaned)

        # 1. Scan os.environ
        for k, v in os.environ.items():
            if sensitive_key_rx.search(k):
                check_and_add(v)

        # 2. Scan .env file if exists
        if self.env_path.exists():
            try:
                for line in self.env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    if sensitive_key_rx.search(key):
                        check_and_add(val)
            except Exception:
                pass

    def scrub(self, text: str) -> str:
        """Sanitize text by redacting structural credentials and discovered secret strings."""
        if not text:
            return ""

        scrubbed = text

        # 1. Structural pattern replacements
        # JWT tokens
        scrubbed = re.sub(
            r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
            "[REDACTED_JWT]",
            scrubbed,
        )
        # API keys with common prefixes (sk_, pk_, etc.)
        scrubbed = re.sub(
            r"(?:sk|pk|api|token|secret)_[A-Za-z0-9_-]{16,}",
            "[REDACTED_API_KEY]",
            scrubbed,
        )
        # HTTP basic auth URLs
        scrubbed = re.sub(
            r"(https?:\/\/)([^:\/\s]+):([^@\/\s]+)@",
            r"\1[REDACTED_USER]:[REDACTED_PASS]@",
            scrubbed,
        )
        # SOCKS5 proxy URLs (standard user:pass@host)
        scrubbed = re.sub(
            r"(socks5h?:\/\/)([^:\/\s]+):([^@\/\s]+)@",
            r"\1[REDACTED_USER]:[REDACTED_PASS]@",
            scrubbed,
        )
        # SOCKS5 pproxy format: socks5://host:port#user:pass
        scrubbed = re.sub(
            r"(socks5h?:\/\/[^#\s]+#)([^:\s]+):([^\s]+)",
            r"\1[REDACTED_USER]:[REDACTED_PASS]",
            scrubbed,
        )

        # 2. Discovered secrets from .env / os.environ (longest first)
        for secret in sorted(self.secrets, key=len, reverse=True):
            scrubbed = scrubbed.replace(secret, "[REDACTED_SECRET]")

        return scrubbed


class TeeStream(io.TextIOBase):
    """
    Tee writer that routes output to the original console stream and an in-memory buffer,
    capping buffer size at 10 MB to prevent memory exhaustion.
    """

    MAX_BUFFER_BYTES = 10 * 1024 * 1024  # 10 MB

    def __init__(self, original_stream: Any, buffer: io.StringIO):
        self.original = original_stream
        self.buffer = buffer
        self.overflow = False
        self.bytes_written = 0

    def write(self, s: str) -> int:
        try:
            self.original.write(s)
            self.original.flush()
        except Exception:
            pass

        if not self.overflow:
            s_len = len(s.encode("utf-8", errors="ignore"))
            if self.bytes_written + s_len > self.MAX_BUFFER_BYTES:
                self.overflow = True
                self.buffer.write(
                    "\n\n... [OUTPUT BUFFER CAPPED AT 10 MB - TRUNCATED] ...\n"
                )
            else:
                self.bytes_written += s_len
                self.buffer.write(s)
        return len(s)

    def flush(self) -> None:
        try:
            self.original.flush()
        except Exception:
            pass
        try:
            self.buffer.flush()
        except Exception:
            pass


class RunTracker:
    """
    Lifecycle manager and context manager for STR price advisor runs.
    """

    def __init__(
        self,
        job_type: str,
        job_title: Optional[str] = None,
        trigger: Optional[str] = None,
        enabled: Optional[bool] = None,
        push: bool = False,
        log_dir: Optional[Path] = None,
        history_path: Optional[Path] = None,
        docs_history_path: Optional[Path] = None,
        scrubber: Optional[SecretScrubber] = None,
    ):
        self.job_type = job_type
        self.job_title = job_title or JOB_TITLES.get(job_type, job_type.replace("-", " ").title())
        self.push = push

        # Resolve trigger
        if trigger:
            self.trigger = trigger
        else:
            self.trigger = os.getenv("STR_TRIGGER", "manual").strip().lower()
        if self.trigger not in ("launchd", "manual"):
            self.trigger = "manual"

        # Resolve tracking gate
        # launchd runs are ALWAYS tracked.
        # manual runs are tracked if enabled=True, push=True, or STR_TRACK=1
        if enabled is not None:
            self.enabled = enabled
        else:
            self.enabled = (
                self.trigger == "launchd"
                or self.push
                or bool(os.getenv("STR_TRACK", "").strip())
            )

        self.log_dir = log_dir or (REPO_ROOT / "docs" / "logs")
        self.history_path = history_path or (REPO_ROOT / "data" / "run_history.json")
        self.docs_history_path = docs_history_path or (REPO_ROOT / "docs" / "data" / "run_history.json")
        self.scrubber = scrubber or SecretScrubber()

        self.run_id: str = ""
        self.start_dt: Optional[datetime] = None
        self.end_dt: Optional[datetime] = None
        self.duration_sec: Optional[float] = None
        self.exit_code: int = 0
        self.status: str = "RUNNING"
        self.summary: Dict[str, Any] = {}
        self.error_excerpt: Optional[str] = None
        self.git_commit: str = "unknown"
        self.log_rel_path: Optional[str] = None

        self._buffer: io.StringIO = io.StringIO()
        self._orig_stdout: Optional[Any] = None
        self._orig_stderr: Optional[Any] = None
        self._tee_stdout: Optional[TeeStream] = None
        self._tee_stderr: Optional[TeeStream] = None

    def set_summary(self, **kwargs: Any) -> None:
        """Update structured domain summary metrics (e.g. intervals_evaluated=12)."""
        self.summary.update(kwargs)

    def set_message(self, msg: str) -> None:
        """Set high-level business summary message for the run record."""
        self.summary["message"] = msg

    def record_error(self, err: BaseException, excerpt: Optional[str] = None) -> None:
        """Explicitly record an exception and construct error excerpt."""
        self.status = "FAILED"
        self.exit_code = 1
        if excerpt:
            self.error_excerpt = excerpt
        else:
            lines = [line for line in str(err).strip().splitlines() if line.strip()]
            if not lines:
                lines = [err.__class__.__name__]
            self.error_excerpt = "\n".join(lines[:3])

    def _get_git_commit(self) -> str:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def __enter__(self) -> "RunTracker":
        if not self.enabled:
            return self

        self.start_dt = datetime.now(timezone.utc)
        ts_str = self.start_dt.strftime("%Y-%m-%d_%H%M%S")
        self.run_id = f"{ts_str}_{self.job_type}"
        self.git_commit = self._get_git_commit()
        self.log_rel_path = f"logs/{self.run_id}.txt"

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.docs_history_path.parent.mkdir(parents=True, exist_ok=True)

        # Intercept stdout and stderr
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._tee_stdout = TeeStream(self._orig_stdout, self._buffer)
        self._tee_stderr = TeeStream(self._orig_stderr, self._buffer)
        sys.stdout = self._tee_stdout
        sys.stderr = self._tee_stderr

        # Record initial RUNNING state in history
        self._persist_history()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if not self.enabled:
            return False

        # Restore streams immediately
        if self._orig_stdout:
            sys.stdout = self._orig_stdout
        if self._orig_stderr:
            sys.stderr = self._orig_stderr

        self.end_dt = datetime.now(timezone.utc)
        if self.start_dt:
            self.duration_sec = round((self.end_dt - self.start_dt).total_seconds(), 1)

        if exc_val is not None:
            self.status = "FAILED"
            self.exit_code = 1
            if not self.error_excerpt:
                tb_lines = traceback.format_exception(exc_type, exc_val, exc_tb)
                clean_tb = "".join(tb_lines).strip()
                # Grab last meaningful lines
                tb_split = [ln for ln in clean_tb.splitlines() if ln.strip()]
                self.error_excerpt = "\n".join(tb_split[-3:]) if tb_split else str(exc_val)
            if not self.summary.get("message"):
                self.summary["message"] = f"Aborted due to error: {exc_val}"
        elif self.status != "FAILED":
            self.status = "SUCCESS"
            self.exit_code = 0
            if not self.summary.get("message"):
                self.summary["message"] = f"{self.job_title} completed successfully."

        # Write sanitized log file
        log_content = self._buffer.getvalue()
        if exc_val is not None and exc_tb is not None:
            log_content += "\n\n" + "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        scrubbed_log = self.scrubber.scrub(log_content)

        log_file_path = self.log_dir / f"{self.run_id}.txt"
        try:
            log_file_path.write_text(scrubbed_log, encoding="utf-8")
        except Exception as e:
            if self._orig_stderr:
                self._orig_stderr.write(f"Error writing log file {log_file_path}: {e}\n")

        # Update run_history.json atomically and prune
        self._persist_history()
        self._prune_retention()

        # Update docs/index.html if present and running against production repo history
        if self.docs_history_path == (REPO_ROOT / "docs" / "data" / "run_history.json"):
            try:
                from src.html_generator import HTMLDashboardGenerator
                HTMLDashboardGenerator.patch_system_tab(REPO_ROOT / "docs" / "index.html")
            except Exception:
                pass

        # Emergency Git Commit & Push if failed and push or launchd trigger is active
        if self.status == "FAILED" and (self.push or self.trigger == "launchd"):
            self._emergency_git_push()

        # Return False so any active exception is re-raised to caller
        return False

    def _to_record(self) -> Dict[str, Any]:
        start_iso = (
            self.start_dt.isoformat().replace("+00:00", "Z")
            if self.start_dt
            else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        end_iso = (
            self.end_dt.isoformat().replace("+00:00", "Z")
            if self.end_dt
            else None
        )
        return {
            "run_id": self.run_id,
            "job_type": self.job_type,
            "job_title": self.job_title,
            "trigger": self.trigger,
            "status": self.status,
            "start_time": start_iso,
            "end_time": end_iso,
            "duration_sec": self.duration_sec,
            "duration_formatted": format_duration(self.duration_sec),
            "exit_code": self.exit_code if self.status != "RUNNING" else None,
            "git_commit": self.git_commit,
            "log_file": self.log_rel_path,
            "summary": self.summary,
            "error_excerpt": self.error_excerpt,
        }

    def _load_store(self, path: Path) -> Dict[str, Any]:
        target = path if path.exists() else self.docs_history_path
        if target.exists():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "runs" in data:
                    return data
            except Exception:
                pass
        return {
            "version": "1.0",
            "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "active_host": get_active_host(),
            "runs": [],
        }

    def _persist_history(self) -> None:
        """Atomically persist current run record into data and docs/data."""
        record = self._to_record()
        store = self._load_store(self.history_path)
        store["version"] = "1.0"
        store["last_updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        store["active_host"] = get_active_host()

        runs = store.get("runs", [])
        # Replace existing entry for this run_id if already present (e.g. was RUNNING), else prepend
        found = False
        for idx, r in enumerate(runs):
            if r.get("run_id") == self.run_id:
                runs[idx] = record
                found = True
                break
        if not found:
            runs.insert(0, record)

        store["runs"] = runs

        self._atomic_json_write(self.history_path, store)
        self._atomic_json_write(self.docs_history_path, store)

    def _atomic_json_write(self, target_path: Path, data: Dict[str, Any]) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        dir_path = target_path.parent
        fd, temp_path = tempfile.mkstemp(dir=dir_path, prefix="run_hist_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(temp_path, target_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def _prune_retention(self) -> None:
        """Prune logs > 14 days and ledger records > 30 days."""
        now = datetime.now(timezone.utc)
        cutoff_logs = now - timedelta(days=14)
        cutoff_records = now - timedelta(days=30)

        # 1. Prune log files in docs/logs
        if self.log_dir.exists():
            for log_file in self.log_dir.glob("*.txt"):
                try:
                    # Match date prefix YYYY-MM-DD
                    m = re.match(r"^(\d{4}-\d{2}-\d{2})", log_file.name)
                    if m:
                        file_date = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        if file_date < cutoff_logs:
                            log_file.unlink(missing_ok=True)
                except Exception:
                    pass

        # 2. Prune records in run_history.json
        store = self._load_store(self.history_path)
        runs = store.get("runs", [])
        valid_runs = []
        for r in runs:
            st = r.get("start_time")
            if st:
                try:
                    # Handle Z or offset
                    clean_st = st.replace("Z", "+00:00")
                    rd = datetime.fromisoformat(clean_st)
                    if rd.tzinfo is None:
                        rd = rd.replace(tzinfo=timezone.utc)
                    if rd >= cutoff_records:
                        valid_runs.append(r)
                except Exception:
                    valid_runs.append(r)
            else:
                valid_runs.append(r)

        if len(valid_runs) != len(runs):
            store["runs"] = valid_runs
            store["last_updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._atomic_json_write(self.history_path, store)
            self._atomic_json_write(self.docs_history_path, store)

    def _emergency_git_push(self) -> None:
        """Surgically stage docs/data/run_history.json and docs/logs/*.txt and push to origin/main."""
        try:
            # Only perform git push if running against actual repo paths
            if self.docs_history_path != (REPO_ROOT / "docs" / "data" / "run_history.json"):
                return

            curr_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
                timeout=5,
            ).stdout.strip()

            if curr_branch != "main":
                return

            # Surgical staging: only existing targets
            stage_targets = []
            if self.docs_history_path.exists():
                stage_targets.append("docs/data/run_history.json")
            if self.log_dir.exists():
                stage_targets.append("docs/logs/")

            if not stage_targets:
                return

            subprocess.run(
                ["git", "add"] + stage_targets,
                check=False,
                cwd=str(REPO_ROOT),
                timeout=10,
            )

            diff_check = subprocess.run(
                ["git", "diff", "--staged", "--quiet"],
                cwd=str(REPO_ROOT),
                timeout=5,
            )
            if diff_check.returncode == 0:
                # Nothing new staged
                return

            # Format Pacific timestamp for alert commit
            try:
                res = subprocess.run(
                    ["date", "+%Y-%m-%d %H:%M:%S %Z"],
                    capture_output=True,
                    text=True,
                    env=dict(os.environ, TZ="America/Los_Angeles"),
                    timeout=5,
                )
                ts_pt = res.stdout.strip()
            except Exception:
                ts_pt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            commit_msg = f"🚨 Automated Alert: {self.job_type} failed with exit code {self.exit_code} ({ts_pt})"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                check=True,
                cwd=str(REPO_ROOT),
                timeout=15,
            )

            # Rebase & Push
            try:
                subprocess.run(
                    ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                    check=True,
                    cwd=str(REPO_ROOT),
                    timeout=30,
                )
            except subprocess.CalledProcessError:
                subprocess.run(["git", "rebase", "--abort"], check=False, cwd=str(REPO_ROOT), timeout=10, stderr=subprocess.DEVNULL)
                return

            subprocess.run(
                ["git", "push", "origin", "main"],
                check=True,
                cwd=str(REPO_ROOT),
                timeout=30,
            )
        except Exception as e:
            if self._orig_stderr:
                self._orig_stderr.write(f"Emergency git push failed: {e}\n")
