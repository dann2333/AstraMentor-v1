/** 头部的账号入口：未登录显示"登录"，已登录显示昵称与退出。 */
import { GraduationCap, LogIn, LogOut, Users } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { useAuth } from '../../contexts/AuthContext';

interface AccountMenuProps {
    onRequestLogin: () => void;
    onOpenClassrooms: () => void;
    /** 首页与学习页的配色不同，交给调用方决定按钮样式。 */
    variant?: 'default' | 'ghost';
}

export function AccountMenu({
    onRequestLogin,
    onOpenClassrooms,
    variant = 'ghost',
}: AccountMenuProps) {
    const { user, isRestoring, logout, isTeacher } = useAuth();

    if (isRestoring) {
        // 恢复会话期间先不渲染按钮，避免"登录"闪一下又变成用户名。
        return <div className="h-9 w-24" aria-hidden="true" />;
    }

    if (!user) {
        return (
            <Button size="sm" variant={variant} onClick={onRequestLogin}>
                <LogIn className="mr-1 h-4 w-4" /> 登录
            </Button>
        );
    }

    return (
        <div className="flex items-center gap-1">
            <Button size="sm" variant={variant} onClick={onOpenClassrooms}>
                {isTeacher ? (
                    <GraduationCap className="mr-1 h-4 w-4" />
                ) : (
                    <Users className="mr-1 h-4 w-4" />
                )}
                {isTeacher ? '我的班级' : '我的作业'}
            </Button>
            <span className="max-w-[10rem] truncate text-sm text-muted-foreground" title={user.username}>
                {user.display_name || user.username}
            </span>
            <Button size="sm" variant={variant} onClick={() => void logout()} title="退出登录">
                <LogOut className="h-4 w-4" />
            </Button>
        </div>
    );
}
