"""把构建好的前端挂到后端上，让一个容器就能跑起整个应用。

平时开发是前端 5173、后端 8000 两个进程，互不相干。但打成镜像发出去时，
让使用者自己再起一个 nginx 或者第二个容器是没必要的负担——前端产物就是
一堆静态文件，FastAPI 直接托管即可，端口只有一个，也没有跨域问题。

这里刻意做成"有就挂、没有就算"：仓库里默认没有 frontend/dist，所以本地
开发和跑测试时这段逻辑完全不生效，行为和以前一样。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, Response
from starlette.types import Scope

logger = logging.getLogger(__name__)

# 仓库根目录（backend/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_STATIC_DIR = PROJECT_ROOT / "frontend" / "dist"


def resolve_static_dir() -> Path | None:
    """返回前端产物目录；没构建过就返回 None。"""
    configured = os.getenv("ASTRA_STATIC_DIR", "").strip()
    candidate = Path(configured).expanduser() if configured else DEFAULT_STATIC_DIR
    if not (candidate / "index.html").is_file():
        return None
    return candidate.resolve()


class SinglePageApp(StaticFiles):
    """静态文件服务，但找不到的路径回退到 index.html。

    前端是单页应用：真实存在的只有 index.html 和 assets/ 下的一堆产物，
    其余路径都由前端自己在浏览器里处理。直接用 StaticFiles 的话，用户刷新
    或者直接访问一个前端内部路径就会吃到 404，所以这里把 404 兜成 index.html
    交给前端。

    注意只兜 404：403 之类的错误照原样抛出，不然真正的权限问题会被伪装成
    "页面正常"。
    """

    def __init__(self, *, directory: Path) -> None:
        super().__init__(directory=directory, html=True)
        self._index = directory / "index.html"

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # index.html 不能被缓存：它里面带着 hash 过的资源名，缓存住了
            # 就会在下次更新后继续去要已经不存在的旧资源。
            return FileResponse(
                self._index,
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )


def mount_frontend(app: FastAPI) -> Path | None:
    """把前端挂到 `/`，返回实际挂载的目录（没挂则返回 None）。

    必须在所有 API 路由注册完之后调用：`/` 上的 Mount 会吃掉一切路径，
    先挂它就会把 /api 和 /docs 一起盖掉。
    """
    static_dir = resolve_static_dir()
    if static_dir is None:
        logger.info("未发现前端产物，仅提供 API（构建前端后即可由同一端口访问页面）")
        return None

    app.mount("/", SinglePageApp(directory=static_dir), name="frontend")
    logger.info("前端已挂载: %s", static_dir)
    return static_dir
