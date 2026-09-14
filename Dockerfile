# syntax=docker/dockerfile:1.7

# AstraMentor 单容器镜像：一个端口同时提供页面和 API。
#
# 分三段：
#   web     —— 构建前端。刻意钉在 $BUILDPLATFORM 上，也就是永远跑在构建机
#              的原生架构。产物是一堆 JS/CSS，和目标架构无关，没必要在
#              QEMU 里把 vite 再跑一遍（arm64 模拟下能慢十倍）。
#   deps    —— 装 Python 依赖到独立的 venv，方便整层拷走，不把编译产物和
#              pip 缓存带进运行镜像。
#   runtime —— 只留运行需要的东西。
#
# 构建：
#   docker build -t astramentor .
#   docker buildx build --platform linux/amd64,linux/arm64 -t <repo> --push .

# ---------------------------------------------------------------- 前端构建
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS web

WORKDIR /web

# 先只拷 lockfile，依赖没变时这层就能命中缓存
COPY frontend/package.json frontend/package-lock.json ./
# 浏览器是运行期才需要的，装依赖时不要顺手下载 Chromium
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci

COPY frontend/ ./

# 前后端同源部署时前端用相对的 /api，不需要传这个变量。
# 只有把前端单独挂到别的域名时才需要指向后端地址。
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build


# ------------------------------------------------------------ Python 依赖
FROM python:3.11-slim-bookworm AS deps

# PyMuPDF / 部分依赖在没有预编译轮子的架构上需要现场编译，
# 所以这一段（且只有这一段）带上编译器。
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ----------------------------------------------------------------- 运行镜像
FROM python:3.11-slim-bookworm AS runtime

# 在线 IDE 会 fork 出子进程编译/运行用户代码，所以镜像里要带：
#
#   bubblewrap    沙箱。使用者的代码一律关在里面跑：没有网络、看不到
#                 /app 和 /data、环境变量清空。它靠内核的非特权 user
#                 namespace 工作，**不需要**给容器加任何 capability，
#                 所以能和应用装在同一个镜像里，不用起第二个容器、
#                 更不用挂 docker socket。
#   node          JavaScript
#   gcc / g++     C / C++
#   golang-go     Go
#   JDK           Java
#
# Go 和 JDK 加起来占掉大半体积。不需要在线 IDE 的话构建时传
# --build-arg WITH_IDE_TOOLCHAIN=false，镜像能小一半以上；那种镜像里
# /api/run-code 会因为沙箱和工具链都不在而直接拒绝。
ARG WITH_IDE_TOOLCHAIN=true

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && if [ "$WITH_IDE_TOOLCHAIN" = "true" ]; then \
         apt-get install -y --no-install-recommends \
           bubblewrap \
           nodejs \
           gcc \
           g++ \
           libc6-dev \
           golang-go \
           default-jdk-headless ; \
       fi \
    && rm -rf /var/lib/apt/lists/*

# 默认整套指向 Kimi K3，所以部署时唯一必须给的就是 ASTRA_API_KEY：
#   docker run -p 8000:8000 -e ASTRA_API_KEY=sk-... <image>
# 密钥不内置在镜像里 —— 镜像是要分发的，烤进去等于公开。没配 Key 时页面
# 照样能打开，只是模型相关的接口会返回一句明确的 503 提示。
#
# ASTRA_DB_PATH / ASTRA_UPLOAD_ROOT 都指向 /data，也就是下面声明的卷，
# 容器重建不丢数据。
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASTRA_PROVIDER=moonshot \
    ASTRA_API_ENDPOINT=https://api.moonshot.cn/v1 \
    ASTRA_MODEL_NAME=kimi-k3 \
    ASTRA_REASONING_EFFORT=low \
    ASTRA_DB_PATH=/data/astramentor.db \
    ASTRA_UPLOAD_ROOT=/data/uploads \
    ASTRA_HOST=0.0.0.0 \
    ASTRA_PORT=8000 \
    ASTRA_SANDBOX_SCRATCH=/sandbox

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app

# 按"越少变动的越先拷"排列，改前端时不会让后端这几层失效
COPY requirements.txt ./
COPY rag/ ./rag/
COPY agents/ ./agents/
COPY core/ ./core/
COPY models/ ./models/
COPY services/ ./services/
COPY utils/ ./utils/
COPY backend/ ./backend/
COPY config.py main.py ./
COPY --from=web /web/dist ./frontend/dist

# 以非 root 运行。/data 是挂载点，先建好并交给该用户，
# 否则匿名卷会以 root 属主挂上来，写库直接 permission denied。
# /sandbox 是在线 IDE 沙箱的工作目录。只读根文件系统下要给它挂一块 tmpfs，
# 而且**必须带 exec** —— Docker 的 --tmpfs 默认是 noexec，那样编译型语言产出
# 的二进制跑不了（bwrap: execvp /work/main: Permission denied）。单独给它一块，
# /tmp 就可以继续保持 noexec。
RUN useradd --create-home --uid 10001 astra \
    && mkdir -p /data /sandbox \
    && chmod 1777 /sandbox \
    && chown -R astra:astra /data /app
USER astra

VOLUME ["/data"]
EXPOSE 8000

# 容器编排靠它判断"能收请求了"。/api/courses 是个只读接口，不碰模型。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${ASTRA_PORT}/api/courses" > /dev/null || exit 1

# 单 worker 是有意为之：课程索引的构建状态存在进程内，多 worker 会让
# 前端轮询到另一个进程、看到"没有在构建"。要横向扩容得先把那份状态
# 挪到 Redis 或数据库里。
CMD ["sh", "-c", "exec uvicorn backend.app:app --host \"$ASTRA_HOST\" --port \"$ASTRA_PORT\" --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
