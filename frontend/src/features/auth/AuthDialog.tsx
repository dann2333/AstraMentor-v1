/** 登录 / 注册对话框。未登录时仍可继续以访客身份使用，因此可以随时关闭。 */
import { useEffect, useState, type FormEvent } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { readableApiError } from '../../api/errors';
import { useAuth } from '../../contexts/AuthContext';

type Mode = 'login' | 'register';

interface AuthDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    initialMode?: Mode;
}

export function AuthDialog({ open, onOpenChange, initialMode = 'login' }: AuthDialogProps) {
    const { login, register } = useAuth();
    const [mode, setMode] = useState<Mode>(initialMode);
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [displayName, setDisplayName] = useState('');
    const [role, setRole] = useState<'student' | 'teacher'>('student');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    // 每次重新打开都回到干净状态，避免上一次的报错或密码留在框里。
    useEffect(() => {
        if (open) {
            setMode(initialMode);
            setUsername('');
            setPassword('');
            setDisplayName('');
            setRole('student');
            setError('');
            setBusy(false);
        }
    }, [open, initialMode]);

    const submit = async (event: FormEvent) => {
        event.preventDefault();
        if (busy) return;
        setBusy(true);
        setError('');
        try {
            if (mode === 'login') {
                await login(username, password);
            } else {
                await register({
                    username,
                    password,
                    display_name: displayName || undefined,
                    role,
                });
            }
            onOpenChange(false);
        } catch (caught) {
            setError(readableApiError(caught, mode === 'login' ? '登录失败' : '注册失败'));
        } finally {
            setBusy(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{mode === 'login' ? '登录' : '注册'}</DialogTitle>
                    <DialogDescription>
                        {mode === 'login'
                            ? '登录后，数据都记在你自己名下。'
                            : '选老师身份，就能建班和批改作业。'}
                    </DialogDescription>
                </DialogHeader>

                <form className="space-y-4" onSubmit={submit}>
                    <div className="space-y-2">
                        <Label htmlFor="auth-username">用户名</Label>
                        <Input
                            id="auth-username"
                            value={username}
                            onChange={(event) => setUsername(event.target.value)}
                            autoComplete="username"
                            required
                            minLength={3}
                            maxLength={32}
                        />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="auth-password">密码</Label>
                        <Input
                            id="auth-password"
                            type="password"
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                            required
                            minLength={mode === 'register' ? 8 : 1}
                            maxLength={128}
                        />
                        {mode === 'register' && (
                            <p className="text-xs text-muted-foreground">至少 8 位。</p>
                        )}
                    </div>

                    {mode === 'register' && (
                        <>
                            <div className="space-y-2">
                                <Label htmlFor="auth-display-name">昵称（可选）</Label>
                                <Input
                                    id="auth-display-name"
                                    value={displayName}
                                    onChange={(event) => setDisplayName(event.target.value)}
                                    maxLength={64}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>身份</Label>
                                <div className="flex gap-2">
                                    {(['student', 'teacher'] as const).map((option) => (
                                        <Button
                                            key={option}
                                            type="button"
                                            variant={role === option ? 'default' : 'outline'}
                                            className="flex-1"
                                            onClick={() => setRole(option)}
                                        >
                                            {option === 'student' ? '学生' : '老师'}
                                        </Button>
                                    ))}
                                </div>
                            </div>
                        </>
                    )}

                    {error && (
                        <p className="text-sm text-destructive" role="alert">
                            {error}
                        </p>
                    )}

                    <Button type="submit" className="w-full" disabled={busy}>
                        {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        {mode === 'login' ? '登录' : '注册'}
                    </Button>
                </form>

                <button
                    type="button"
                    className="text-sm text-muted-foreground underline-offset-4 hover:underline"
                    onClick={() => {
                        setMode(mode === 'login' ? 'register' : 'login');
                        setError('');
                    }}
                >
                    {mode === 'login' ? '没有账号，去注册' : '已经有账号了'}
                </button>
            </DialogContent>
        </Dialog>
    );
}
