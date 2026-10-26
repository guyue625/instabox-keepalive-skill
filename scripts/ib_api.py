"""InstaBox API 调用。凭证走保险库 surrogate 交换，不碰明文 key。

可移植性：dynamic_credentials 的搜索路径可用环境变量
IB_CREDENTIALS_BIN 覆盖（默认仍是 Muse 的 custom-connector-setup 路径）。
模块不存在时不让 import 直接崩溃——推迟到真正调 API 时才报
DynamicCredentialError，这样 decide() 等纯逻辑可以离线单测。
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

_CRED_BIN = os.environ.get(
    "IB_CREDENTIALS_BIN",
    "/opt/muse/skills/custom-connector-setup/bin",
)
if _CRED_BIN not in sys.path:
    sys.path.insert(0, _CRED_BIN)

try:
    from dynamic_credentials import (  # noqa: E402
        add_surrogate_to_request,
        read_response_body,
        DynamicCredentialError,
    )
    _CRED_IMPORT_ERROR = None
except Exception as _e:  # pragma: no cover - 只在非 Muse 环境出现
    _CRED_IMPORT_ERROR = _e

    class DynamicCredentialError(RuntimeError):
        pass

    def add_surrogate_to_request(req, cred, allowed_hosts=None):  # noqa: ARG001
        raise DynamicCredentialError(
            f"dynamic_credentials 不可用（{_CRED_IMPORT_ERROR}）；"
            "请确认 IB_CREDENTIALS_BIN 或在 Muse 环境中运行"
        )

    def read_response_body(resp):
        return resp.read()


BASE = "https://api.instabox.sh"
HOSTS = ["api.instabox.sh"]
LIFECYCLE = ("/suspend", "/resume", "/start", "/stop")


class ApiError(RuntimeError):
    pass


def call(method, path, cred, body=None, timeout=60, idempotency_key=None):
    """调一次 API。

    idempotency_key：生命周期写操作（suspend/resume/start/stop）用。
    调用方同一次逻辑操作的重试应传入同一个 key，服务端才会去重；
    不传时才新生成一个（旧行为）。非生命周期操作忽略该参数。
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if path.endswith(LIFECYCLE):
        req.add_header("Idempotency-Key", idempotency_key or str(uuid.uuid4()))
    add_surrogate_to_request(req, cred, allowed_hosts=HOSTS)
    try:
        with urllib.request.build_opener().open(req, timeout=timeout) as resp:
            return json.loads(read_response_body(resp).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}: {read_response_body(e).decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"网络错误: {e}") from e
