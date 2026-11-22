"""子进程环境白名单 —— 一份实现，四处共用。

`readiness` / `selfcheck` / `decision_points` / `mutation_check` 以前各写一份
`{k: v for k, v in os.environ.items() if ...}`。口径很快就漂了：
`readiness` 按文档去掉了 `NPM_`/`PNPM_`/`YARN_`/`PIP_`/`UV_` 前缀（它们带着
仓库凭据 —— npm token、pip index 里的密码 —— 进子进程），`mutation_check`
与 `decision_points` 却还留着 ⇒ 那两处的白名单形同虚设。

现在白名单只在这里定义一次；调用方只给**差异**（要清空的键、要追加的键），
不再各写一份筛选逻辑。
"""
from __future__ import annotations

import os

#: 精确放行 —— 与运行有关的变量，外加工具自己要的。
ALLOW_EXACT = frozenset({
    "PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP",
    "SHELL", "USER", "LOGNAME", "TERM", "SYSTEMROOT", "COMSPEC", "CI",
    "POSTGRES_URL", "DATABASE_URL", "MEMORY_ITEST",
    "MEMORY_RECONCILE_OUT_DIR", "REQ_PLAN_RECONCILE_OUT_DIR",
})

#: 前缀放行 —— **只放运行时配置，不放凭据**。
#: `NPM_`/`PNPM_`/`YARN_`/`PIP_`/`UV_` 不在这里：它们会带着仓库凭据
#: （`NPM_TOKEN`、`PIP_INDEX_URL` 里的密码、`UV_INDEX_SECRET_PASSWORD`）进子进程。
#: 仓库的 test 命令需要 npm/pip 配置时，用配置文件（.npmrc / pip.conf）而不是环境变量。
ALLOW_PREFIX = ("LC_", "NODE_", "PYTHON")

#: 命中前缀也**不**透传的凭据类变量（npm 的 registry token 就在 `NODE_` 前缀里）。
DENY = frozenset({"NODE_AUTH_TOKEN"})


def child_env(extra: dict[str, str] | None = None, *,
              allow_exact=ALLOW_EXACT, allow_prefix=ALLOW_PREFIX, deny=DENY,
              clear: tuple[str, ...] = ()) -> dict[str, str]:
    """按白名单组一份子进程环境。

    extra：追加/覆盖的键。clear：即使在白名单里也要置空的键
    （自检副本不该去连用户的库）。
    """
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k in deny:
            continue
        if k in allow_exact or k.startswith(allow_prefix):
            env[k] = v
    env["PYTHONUTF8"] = "1"
    for k in clear:
        env[k] = ""
    if extra:
        for k, v in extra.items():
            if not isinstance(v, str):
                # subprocess 收到非字符串的值会抛一个看不懂的错 —— 在这儿说清
                raise TypeError(f"环境变量的值必须是字符串：{k}")
            env[k] = v
    return env
