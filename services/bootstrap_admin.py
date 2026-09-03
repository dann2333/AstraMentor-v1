"""授予第一个管理员账号。

``admin`` 只能由已有管理员通过 ``PUT /api/admin/users/{id}/role`` 授予 —— 这是
故意的，否则任何人注册时都能自封管理员。但这也意味着全新部署里一个管理员都
没有，那条接口永远调不通，也就没人能把某个学生改成老师。

引导第一个管理员必须走本地命令行：能执行它就已经拥有服务器和数据库文件了，
不构成新的攻击面。

    python -m services.bootstrap_admin <用户名>

账号必须先通过正常注册流程创建。
"""

from __future__ import annotations

import argparse
import sys

from services.account_service import AccountService, UserNotFound, account_service
from services.database import ROLE_ADMIN


def promote(username: str, service: AccountService | None = None) -> str:
    """把已存在的账号提升为管理员，返回其账号 id。"""
    service = service or account_service
    user = service.find_by_username(username)
    if user is None:
        raise UserNotFound(username)
    service.set_role(user.id, ROLE_ADMIN)
    return user.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m services.bootstrap_admin",
        description="把一个已注册的账号提升为管理员",
    )
    parser.add_argument("username", help="要提升的账号用户名")
    args = parser.parse_args(argv)

    try:
        user_id = promote(args.username)
    except UserNotFound:
        print(
            f"找不到账号 {args.username!r}。请先通过 /api/auth/register 注册，再运行本命令。",
            file=sys.stderr,
        )
        return 1
    print(f"✅ {args.username} 已成为管理员（id={user_id}）。")
    print("   现在可以通过 PUT /api/admin/users/{id}/role 调整其他账号的角色。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
