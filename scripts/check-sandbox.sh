#!/usr/bin/env bash
# 在你自己的机器上测一遍：在线 IDE 的 bubblewrap 沙箱能不能起来，
# 以及最少要给容器放开什么。
#
#   bash scripts/check-sandbox.sh                    # 用 GHCR 上的镜像
#   bash scripts/check-sandbox.sh astramentor:local  # 用本地构建的镜像
#
# 为什么需要这个脚本：沙箱靠内核的非特权 user namespace，而能不能建出来取决于
# 宿主机的内核和 Docker 的默认 seccomp / AppArmor 策略 —— 不同发行版差别很大，
# 只能实测，猜不出来。脚本只读不写，不会改你的配置。
set -uo pipefail

IMAGE="${1:-ghcr.io/dann2333/astramentor-v1:latest}"

command -v docker > /dev/null || { echo "没找到 docker"; exit 1; }

echo "镜像: $IMAGE"
echo
echo "宿主机相关设置："
for key in kernel.unprivileged_userns_clone \
           kernel.apparmor_restrict_unprivileged_userns \
           kernel.apparmor_restrict_unprivileged_unconfined \
           user.max_user_namespaces; do
  printf '  %-52s %s\n' "$key" "$(sysctl -n "$key" 2>/dev/null || echo '（不存在）')"
done
echo

docker image inspect "$IMAGE" > /dev/null 2>&1 || {
  echo "本地没有这个镜像，拉一下…"
  docker pull "$IMAGE" > /dev/null || { echo "拉取失败"; exit 1; }
}

# 和服务端判断沙箱可用性走的是同一段代码
PROBE_PY='from services.sandbox import probe; ok, d = probe(); print(("OK" if ok else "FAIL") + " " + d)'

# compose 里那套加固参数，作为固定基线
# 注意 /sandbox 那块必须带 exec：Docker 的 tmpfs 默认 noexec，那样编译型
# 语言产出的二进制跑不了（bwrap: execvp /work/main: Permission denied）。
HARD="--read-only --tmpfs /tmp:size=64m,mode=1777 --tmpfs /sandbox:size=64m,mode=1777,exec --cap-drop ALL --security-opt no-new-privileges:true"

WINNER=""
WINNER_YAML=""

check() {
  local label="$1" flags="$2" yaml="$3" out brief
  # shellcheck disable=SC2086
  out=$(docker run --rm -w /app $HARD $flags "$IMAGE" python -c "$PROBE_PY" 2>&1 | tail -3 | tr '\n' ' ')
  case "$out" in
    OK*)
      printf '  ✅ %s\n' "$label"
      [ -z "$WINNER" ] && { WINNER="$label"; WINNER_YAML="$yaml"; }
      ;;
    *)
      brief=$(printf '%s' "$out" | sed 's/。最常见的原因.*//; s/^FAIL //')
      printf '  ❌ %s\n     %s\n' "$label" "$brief"
      ;;
  esac
}

echo "逐个试（都带着 compose 那套只读根 + cap_drop ALL 的加固）："
check "什么都不额外放开" \
      "" \
      ""
check "seccomp=unconfined" \
      "--security-opt seccomp=unconfined" \
      "      - seccomp=unconfined"
check "seccomp=unconfined + apparmor=unconfined" \
      "--security-opt seccomp=unconfined --security-opt apparmor=unconfined" \
      "      - seccomp=unconfined
      - apparmor=unconfined"
check "seccomp + apparmor + systempaths 都 unconfined" \
      "--security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined" \
      "      - seccomp=unconfined
      - apparmor=unconfined
      - systempaths=unconfined"
echo

if [ -n "$WINNER" ]; then
  echo "结论：沙箱可用，最少只需「$WINNER」。"
  if [ -z "$WINNER_YAML" ]; then
    echo "docker-compose.yml 不用改，直接 docker compose up -d 即可。"
  else
    echo
    echo "把 docker-compose.yml 里 security_opt 那一段改成："
    echo
    echo "    security_opt:"
    echo "      - no-new-privileges:true"
    echo "$WINNER_YAML"
    echo
    echo "这些只放开这一个容器的 LSM 策略，不授予任何 capability"
    echo "（cap_drop: ALL 仍然生效）。"
  fi
else
  cat <<'EOF'
结论：这台机器上沙箱起不来。

如果上面 kernel.apparmor_restrict_unprivileged_userns 显示为 1（Ubuntu 23.10
起的默认值），可以先试试把它放开再重跑本脚本 —— 这一项影响整台机器上所有
程序，影响面比容器 flag 大，自己权衡：

    sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0

服务不会因此裸跑 —— /api/run-code 会直接拒绝执行并在日志里说明原因，
在线 IDE 相当于不可用。两个选择：

  1. 不要在线 IDE，直接关掉它（推荐，最省事）：
       echo 'ASTRA_CODE_RUNNER_ENABLED=false' >> .env
       docker compose up -d

  2. 想要在线 IDE，就得换一层更强的隔离来跑它，比如 gVisor（runsc）
     或 Kata 这类容器运行时，而不是把上面那些 flag 一路放开到
     --privileged —— 那等于拿宿主机换一个功能。
EOF
  exit 2
fi
