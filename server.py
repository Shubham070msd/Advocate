#!/usr/bin/env python3
"""
Advocate — web server (agent wired in).
========================================

Serves the dashboard and a storage-backed API. The agent runs on a background
thread per case. Human-in-the-loop approve/reject triggers a resume thread.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from advocate.models import Case, ResolutionPolicy
from advocate.store import CaseStore

DB_PATH = os.environ.get("ADVOCATE_DB", "advocate.db")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run_agent(case_id: str) -> None:
    """Run the autonomous resolution agent for a Case on a background thread."""
    from advocate.agent.orchestrator import resolve_case
    resolve_case(case_id, DB_PATH)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        if path == "/api/cases":
            store = CaseStore(DB_PATH)
            try:
                return self._json([{"case_id": c.case_id, "status": c.status, "goal": c.goal,
                                    "outcome_amount": c.outcome_amount, "outcome_kind": c.outcome_kind,
                                    "currency": c.policy.currency, "updated_at": c.updated_at}
                                   for c in store.list()])
            finally:
                store.close()
        if path.startswith("/api/cases/"):
            store = CaseStore(DB_PATH)
            try:
                c = store.get(path[len("/api/cases/"):])
                return self._json(c.to_dict() if c else {"error": "not found"}, 200 if c else 404)
            finally:
                store.close()
        return self.send_error(404)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/cases":
            spec = self._body()
            ctx = spec.get("context", {})
            ctx["_counterparty_mode"] = spec.get("counterparty", "llm")
            case = Case(goal=spec.get("goal", ""),
                        policy=ResolutionPolicy.from_dict(spec.get("policy", {})),
                        context=ctx, evidence=spec.get("evidence", []))
            store = CaseStore(DB_PATH)
            store.save(case)
            store.close()
            threading.Thread(target=run_agent, args=(case.case_id,), daemon=True).start()
            return self._json({"case_id": case.case_id})

        if path.startswith("/api/cases/") and path.endswith("/approve"):
            case_id = path[len("/api/cases/"):-len("/approve")]
            body = self._body()
            store = CaseStore(DB_PATH)
            try:
                case = store.get(case_id)
                if case:
                    # Record the human's decision
                    approved = body.get("approved", False)
                    note = body.get("note", "")
                    reply_text = str(approved) if not note else note
                    case.add_message("system", "User reply: %s" % reply_text)
                    store.save(case)
            finally:
                store.close()

            # Resume the agent on a new background thread
            threading.Thread(target=run_agent, args=(case_id,), daemon=True).start()

            return self._json({"ok": True})
        return self.send_error(404)


def main():
    load_dotenv()
    port = int(os.environ.get("PORT", "8000"))
    print("Advocate dashboard at http://localhost:%d  (Ctrl+C to stop)" % port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
