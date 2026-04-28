from __future__ import annotations

import json
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config_loader import BASE_DIR, CONFIG_DIR, load_json, save_json
from scheduler import get_session_type

HOST = "127.0.0.1"
PORT = 8765
WEB_DIR = BASE_DIR / "web"
REPORTS_DIR = BASE_DIR / "reports"
TASK_NAMES = {
    "am": "stock-watchtower-am",
    "pm": "stock-watchtower-pm",
}


def _load_configs() -> dict:
    return {
        "portfolio": load_json(CONFIG_DIR / "portfolio.json"),
        "watchlist": load_json(CONFIG_DIR / "watchlist.json"),
        "strategy": load_json(CONFIG_DIR / "strategy.json"),
    }


def _recent_reports(limit: int = 12) -> list[dict]:
    files = sorted(REPORTS_DIR.glob("run_*.md"), reverse=True)[:limit]
    return [
        {
            "name": file.name,
            "path": f"/reports/{file.name}",
            "modified": file.stat().st_mtime,
        }
        for file in files
    ]


def _query_task(task_name: str) -> dict:
    command = ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        return {
            "exists": False,
            "task_name": task_name,
            "status": "unknown",
            "next_run": "-",
            "raw": result.stderr.strip() or result.stdout.strip(),
        }

    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()

    return {
        "exists": True,
        "task_name": task_name,
        "status": fields.get("Status", "Unknown"),
        "enabled": fields.get("Scheduled Task State", "").lower() == "enabled",
        "next_run": fields.get("Next Run Time", "-"),
        "last_run": fields.get("Last Run Time", "-"),
        "last_result": fields.get("Last Result", "-"),
        "schedule_type": fields.get("Schedule Type", "-"),
        "start_time": fields.get("Start Time", "-"),
        "repeat": fields.get("Repeat: Every", "-"),
        "duration": fields.get("Repeat: Until: Duration", "-"),
    }


def _tasks_state() -> dict:
    return {key: _query_task(name) for key, name in TASK_NAMES.items()}


def _run_task_action(task_key: str, action: str) -> dict:
    task_name = TASK_NAMES[task_key]
    if action == "enable":
        command = ["schtasks", "/Change", "/TN", task_name, "/ENABLE"]
    elif action == "disable":
        command = ["schtasks", "/Change", "/TN", task_name, "/DISABLE"]
    elif action == "run":
        command = ["schtasks", "/Run", "/TN", task_name]
    else:
        raise ValueError("Unsupported action")

    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "task": _query_task(task_name),
    }


def _run_once() -> dict:
    result = subprocess.run(
        [sys.executable, "src/main.py"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=240,
    )
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "latest_report": _recent_reports(1),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "StockWatchtowerDashboard/1.0"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path, content_type: str) -> None:
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._send_file(WEB_DIR / "dashboard.html", "text/html; charset=utf-8")
        if parsed.path == "/api/state":
            session_type = get_session_type()
            return self._send_json(
                {
                    "ok": True,
                    "configs": _load_configs(),
                    "tasks": _tasks_state(),
                    "reports": _recent_reports(),
                    "env_exists": (BASE_DIR / ".env").exists(),
                    "session": {
                        "type": session_type,
                        "label": {"pre_market": "盘前分析", "in_session": "盘中巡检", "closed": "已闭市"}.get(session_type, session_type),
                    },
                }
            )
        if parsed.path.startswith("/reports/"):
            target = REPORTS_DIR / Path(parsed.path).name
            if target.exists():
                return self._send_file(target, "text/markdown; charset=utf-8")
            return self._send_json({"ok": False, "message": "报告不存在"}, status=404)

        return self._send_json({"ok": False, "message": "Not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
            if parsed.path == "/api/configs":
                portfolio = payload.get("portfolio")
                watchlist = payload.get("watchlist")
                strategy = payload.get("strategy")
                if not isinstance(portfolio, dict) or not isinstance(watchlist, dict) or not isinstance(strategy, dict):
                    return self._send_json({"ok": False, "message": "配置格式错误，必须是合法 JSON 对象。"}, status=400)
                save_json(CONFIG_DIR / "portfolio.json", portfolio)
                save_json(CONFIG_DIR / "watchlist.json", watchlist)
                save_json(CONFIG_DIR / "strategy.json", strategy)
                return self._send_json({"ok": True, "message": "配置已保存。"})

            if parsed.path == "/api/task-action":
                task_key = payload.get("task")
                action = payload.get("action")
                if task_key not in TASK_NAMES:
                    return self._send_json({"ok": False, "message": "未知任务。"}, status=400)
                return self._send_json({"ok": True, "result": _run_task_action(task_key, action)})

            if parsed.path == "/api/run-once":
                return self._send_json({"ok": True, "result": _run_once()})
        except subprocess.TimeoutExpired:
            return self._send_json({"ok": False, "message": "执行超时，请稍后再试。"}, status=500)
        except Exception as exc:
            return self._send_json({"ok": False, "message": str(exc)}, status=500)

        return self._send_json({"ok": False, "message": "Not found"}, status=404)


def main() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Dashboard running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
