import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from backend.api import router
from backend.doc_api import doc_router
from backend.course_api import course_router
from backend.session_api import session_router
from backend.auth_api import auth_router
from backend.user_data_api import user_data_router
from backend.classroom_api import admin_router, classroom_router
from backend.assignment_api import assignment_router
from rag.errors import CourseIndexNotReadyError
from services.learning_store import PayloadTooLarge
from utils.api_client import MissingAPIKey
from config import get_config
from services.legacy_import import import_legacy_data
from backend.static_site import mount_frontend

# NOTE: 配置日志级别，确保项目模块的 INFO 日志可见
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


def _import_legacy_data_once() -> None:
    """把改造前留在磁盘上的 JSON 数据搬进 SQLite。

    改造前的星图、学习状态、会话与文档都没有归属信息，因此统一导入到预留的
    访客账号下。导入器会把源目录改名，所以这件事只会真正做一次；没有旧数据
    时它连数据库都不会碰。设 ASTRA_SKIP_LEGACY_IMPORT=true 可以跳过。
    """
    if os.getenv("ASTRA_SKIP_LEGACY_IMPORT", "").lower() == "true":
        return
    try:
        summary = import_legacy_data()
    except Exception:  # pragma: no cover - 导入失败绝不能挡住服务启动
        logger.exception("旧数据导入失败，已跳过；服务继续启动")
        return
    if any(summary.values()):
        logger.info(
            "旧数据已导入访客空间: 会话 %d / 星图 %d / 学习状态 %d / 文档 %d",
            summary["sessions"],
            summary["graphs"],
            summary["learner_states"],
            summary["documents"],
        )


def _log_model_config() -> None:
    """启动时把模型配置打出来，并在缺 Key 时明确说一句。

    镜像默认整套指向 Kimi K3，部署者只需要给一个 ASTRA_API_KEY。把这件事
    写在启动日志的第一屏，比让人先点一下界面、撞到报错再去翻文档要省事。
    """
    api = get_config().api
    logger.info(
        "模型配置: provider=%s model=%s endpoint=%s reasoning_effort=%s",
        api.provider,
        api.model_name,
        api.api_endpoint,
        api.reasoning_effort,
    )
    if api.api_key:
        return
    logger.warning(
        "未检测到 ASTRA_API_KEY —— 页面能打开，但生成星图、讲解、出题都会"
        "返回 503。设置该环境变量后重启即可"
        "（Docker: docker run -e ASTRA_API_KEY=sk-... ...）。"
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _log_model_config()
    _import_legacy_data_once()
    yield


app = FastAPI(title="AstraMentor API", version="1.0.0", lifespan=lifespan)



@app.exception_handler(PayloadTooLarge)
async def payload_too_large_handler(
    _request: Request, exc: PayloadTooLarge
) -> JSONResponse:
    """存储体积超限一律 413。

    学习者状态是在业务代码深处由 ``_auto_save()`` 写入的，超限异常会从任意一个
    学习接口冒出来。逐个路由去 try 既容易漏，也会把这条规则散开；在这里兜一次，
    任何路径下的超大写入都得到同一个明确答复，而不是一个 500。
    """
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(MissingAPIKey)
async def missing_api_key_handler(
    _request: Request, exc: MissingAPIKey
) -> JSONResponse:
    """没配 Key 时给一句能照着做的话，而不是 500。

    每个需要模型的接口都会在构造 APIClient 时撞上这个异常，所以在这里兜一次
    就够了。用 503 而不是 500：服务本身是好的，缺的是一项配置，补上重启即可。
    """
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(CourseIndexNotReadyError)
async def course_index_not_ready_handler(
    _request: Request, exc: CourseIndexNotReadyError
) -> JSONResponse:
    """Expose one stable recovery contract for all course-mode endpoints."""
    return JSONResponse(status_code=409, content={"detail": exc.to_detail()})

# 跨域。单容器部署时前端和 API 同源，本来不需要放开跨域；这里保留配置
# 是为了前后端分开部署的场景。默认 "*" 只适合本机开发，公网部署应该用
# ASTRA_CORS_ORIGINS 填上自己的域名。
_cors_origins = get_config().server.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # 令牌是放在请求头里的 Bearer，不依赖 cookie。配了具体域名之后就没必要
    # 再让浏览器带凭证跨域了，少一条可被利用的路径。
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(doc_router, prefix="/api/doc")
app.include_router(course_router, prefix="/api")
app.include_router(session_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(user_data_router, prefix="/api")
app.include_router(classroom_router, prefix="/api")
app.include_router(assignment_router, prefix="/api")
app.include_router(admin_router, prefix="/api")

# 前端挂在最后：`/` 上的 Mount 会匹配一切路径，放在路由之前会把 /api 和
# /docs 一起盖掉。仓库里默认没有 frontend/dist，所以这行在开发和测试环境
# 是空操作。
mount_frontend(app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
