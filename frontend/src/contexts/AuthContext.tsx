/**
 * 登录状态。
 *
 * 未登录不是错误状态：后端允许访客使用学习功能（数据落在共享的访客空间），
 * 因此这里区分三种状态 —— 正在恢复会话 / 已登录 / 访客。
 */
import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useState,
    type ReactNode,
} from 'react';
import { configureAuthBridge } from '../api/client';
import {
    authApi,
    onTokenChange,
    readToken,
    writeToken,
    type AuthUser,
    type RegisterPayload,
} from '../api/auth';

interface AuthContextValue {
    user: AuthUser | null;
    /** 首次挂载时会拿着已存的令牌去换用户信息，这段时间为 true。 */
    isRestoring: boolean;
    isAuthenticated: boolean;
    isTeacher: boolean;
    login: (username: string, password: string) => Promise<AuthUser>;
    register: (payload: RegisterPayload) => Promise<AuthUser>;
    logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// client.ts 不认识 localStorage，这里把读取与失效回调注入进去。
// 放在模块顶层是为了在任何组件发起第一个请求之前就完成注入。
configureAuthBridge({
    readAccessToken: readToken,
    onUnauthorized: () => writeToken(null),
});

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<AuthUser | null>(null);
    const [isRestoring, setIsRestoring] = useState(() => Boolean(readToken()));

    // 令牌被拦截器清掉（401）时同步退出登录状态。
    useEffect(() => onTokenChange((token) => {
        if (!token) setUser(null);
    }), []);

    // 刷新页面后用已存的令牌恢复身份；令牌无效时拦截器会顺手清掉它。
    useEffect(() => {
        let cancelled = false;
        // 没有令牌时 isRestoring 的初值本来就是 false，这里直接跳过即可。
        if (!readToken()) return;
        authApi
            .me()
            .then((profile) => {
                if (!cancelled) setUser(profile);
            })
            .catch(() => {
                if (!cancelled) setUser(null);
            })
            .finally(() => {
                if (!cancelled) setIsRestoring(false);
            });
        return () => {
            cancelled = true;
        };
    }, []);

    const login = useCallback(async (username: string, password: string) => {
        const result = await authApi.login(username, password);
        writeToken(result.access_token);
        setUser(result.user);
        return result.user;
    }, []);

    const register = useCallback(async (payload: RegisterPayload) => {
        const result = await authApi.register(payload);
        writeToken(result.access_token);
        setUser(result.user);
        return result.user;
    }, []);

    const logout = useCallback(async () => {
        try {
            await authApi.logout();
        } catch {
            // 令牌可能已经过期，本地照样要清干净。
        }
        writeToken(null);
        setUser(null);
    }, []);

    const value = useMemo<AuthContextValue>(
        () => ({
            user,
            isRestoring,
            isAuthenticated: user !== null,
            isTeacher: user?.role === 'teacher' || user?.role === 'admin',
            login,
            register,
            logout,
        }),
        [user, isRestoring, login, register, logout],
    );

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
    const context = useContext(AuthContext);
    if (!context) throw new Error('useAuth 必须在 AuthProvider 内使用');
    return context;
}
