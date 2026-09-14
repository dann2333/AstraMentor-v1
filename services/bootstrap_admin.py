"""建出（或提升出）第一个管理员账号。

## 为什么需要命令行

``admin`` 只能由已有管理员通过 ``PUT /api/admin/users/{id}/role`` 授予 —— 这是
故意的，否则任何人注册时都能自封管理员。但这也意味着全新部署里一个管理员都
没有，那条接口永远调不通，也就没人能把某个学生改成老师。

引导第一个管理员必须走本地命令行：能执行它就已经拥有服务器和数据库文件了，
不构成新的攻击面。

## 用法

    python -m services.bootstrap_admin <用户名>

账号不存在就顺手建出来（问一次密码），已存在就直接提升。所以关掉自助注册
（``ASTRA_REGISTRATION_ENABLED=false``）的部署也能有第一个账号 —— 之前这里
只能提升已有账号，那种部署等于一个号都建不出来。

容器里：

    docker compose exec astramentor python -m services.bootstrap_admin <用户名>

非交互场景（CI、脚本）可以用 ``--password``，或者干脆不给：不是终端时会生成
一个随机强密码并打印出来。
"""

from __future__ import annotations

import argparse
import secrets
import sys
from getpass import getpass

from services.account_service import (
    AccountService,
    UserNotFound,
    ValidationError,
    account_service,
    validate_password,
)
from services.database import ROLE_ADMIN

#: 生成随机密码的长度（token_urlsafe 的参数是字节数，输出更长）
_GENERATED_PASSWORD_BYTES = 18


def promote(username: str, service: AccountService | None = None) -> str:
    """把已存在的账号提升为管理员，返回其账号 id。

    账号不存在时抛 UserNotFound —— 建号是 ensure_admin() 的事，这里只提升。
    """
    service = service or account_service
    user = service.find_by_username(username)
    if user is None:
        raise UserNotFound(username)
    service.set_role(user.id, ROLE_ADMIN)
    return user.id


def ensure_admin(
    username: str,
    password: str | None = None,
    service: AccountService | None = None,
) -> tuple[str, bool]:
    """保证存在一个叫 username 的管理员账号。

    返回 ``(账号 id, 是否新建)``。已存在就提升（忽略 password）；不存在就按
    给定密码建号并直接设成 admin。
    """
    service = service or account_service
    user = service.find_by_username(username)
    if user is not None:
        service.set_role(user.id, ROLE_ADMIN)
        return user.id, False

    if not password:
        raise ValidationError("新建账号必须给密码")
    created = service.register(
        username,
        password,
        role=ROLE_ADMIN,
        # 只有这条命令行能传 True —— HTTP 注册接口永远不传。
        allow_admin_role=True,
    )
    return created.id, True


def _ask_password() -> tuple[str, bool]:
    """取一个新账号的密码，返回 ``(密码, 是否是随机生成的)``。

    不是终端时（容器里跑脚本、CI）不能干等着 getpass 卡死，生成一个随机的
    并让调用方打印出来。
    """
    if not sys.stdin.isatty():
        return secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES), True

    for _ in range(3):
        first = getpass("为新账号设置密码：")
        if first != getpass("再输一次："):
            print("两次输入不一致，重来。", file=sys.stderr)
            continue
        try:
            validate_password(first)
        except ValidationError as exc:
            print(f"密码不合要求：{exc}", file=sys.stderr)
            continue
        return first, False

    raise ValidationError("密码输入失败次数过多")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m services.bootstrap_admin",
        description="建出（或提升出）第一个管理员账号",
    )
    parser.add_argument("username", help="管理员的用户名")
    parser.add_argument(
        "--password",
        help="新建账号时用的密码。不给就交互式询问；不是终端时随机生成并打印。",
    )
    parser.add_argument(
        "--promote-only",
        action="store_true",
        help="只提升已有账号，不存在就报错（不建号）",
    )
    args = parser.parse_args(argv)

    if args.promote_only:
        try:
            user_id = promote(args.username)
        except UserNotFound:
            print(f"找不到账号 {args.username!r}。", file=sys.stderr)
            return 1
        print(f"✅ {args.username} 已成为管理员（id={user_id}）。")
        return 0

    password = args.password
    generated = False
    if password is None and account_service.find_by_username(args.username) is None:
        try:
            password, generated = _ask_password()
        except ValidationError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    try:
        user_id, created = ensure_admin(args.username, password)
    except ValidationError as exc:
        print(f"建号失败：{exc}", file=sys.stderr)
        return 1

    if created:
        print(f"✅ 已新建管理员 {args.username}（id={user_id}）。")
        if generated:
            print(f"   随机密码：{password}")
            print("   请立刻登录并改掉它。")
    else:
        print(f"✅ 已有账号 {args.username} 提升为管理员（id={user_id}）。")
    print("   改别人的角色走 PUT /api/admin/users/{id}/role（目前没有管理界面）。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
