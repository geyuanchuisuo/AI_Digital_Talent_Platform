"""可复用假 OpenAI 兼容 LLM server（T-D00f）。

三态注入：success（正常返回）/ timeout（挂起超过客户端超时）/ malformed（非 JSON
或缺失 choices）。从 backend/tests/test_llm_client_fake_server.py 的模式改编为
可导入模块，供 tests/D 与后续 D 域任务复用。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal

Mode = Literal["success", "timeout", "malformed"]


def chat_body(content: str, model: str = "fake-model") -> dict:
    """OpenAI chat.completions 标准成功响应体。"""
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def error_body(message: str) -> dict:
    return {"error": {"message": message, "type": "fake_error"}}


class FakeLLMServer:
    """本机随机端口起 HTTP server；set_mode 切换三态，base_url 供客户端接入。"""

    def __init__(self, mode: Mode = "success") -> None:
        self._mode: Mode = mode
        self._lock = threading.Lock()
        self.requests: list[dict] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    payload = {}
                with outer._lock:
                    outer.requests.append(payload)
                    mode = outer._mode
                if mode == "timeout":
                    time.sleep(3)  # 超过测试客户端的超时设置
                    status, body = 200, chat_body("late")
                elif mode == "malformed":
                    status, body = 200, {"unexpected": "shape"}  # 缺 choices
                else:
                    status, body = 200, chat_body("fake-ok")
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # 静音
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def set_mode(self, mode: Mode) -> None:
        with self._lock:
            self._mode = mode

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
