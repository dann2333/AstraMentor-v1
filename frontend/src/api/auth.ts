/**
 * 令牌存放与鉴权接口。
 *
 * 令牌只放在 localStorage，并由 client.ts 的请求拦截器统一挂到 Authorization 头上，
 * 避免每个调用点各写一遍、漏掉一处就退回访客身份。
 */
import { client } from './client';

const TOKEN_STORAGE_KEY = 'astramentor.access_token';

export type UserRole = 'student' | 'teacher' | 'admin';

export interface AuthUser {
    id: string;
    username: string;
    email: string | null;
    display_name: string;
    is_active: boolean;
    role: UserRole;
    created_at: string;
    updated_at: string;
    last_login_at: string | null;
}

export interface TokenResponse {
    access_token: string;
    token_type: string;
    expires_at: string;
    user: AuthUser;
}

/** 令牌失效时通知 UI 退出登录，而不是让后续请求一路 401。 */
type TokenListener = (token: string | null) => void;
const listeners = new Set<TokenListener>();

export function readToken(): string | null {
    try {
        return localStorage.getItem(TOKEN_STORAGE_KEY);
    } catch {
        // 隐私模式下 localStorage 可能直接抛错，此时按未登录处理。
        return null;
    }
}

export function writeToken(token: string | null): void {
    try {
        if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token);
        else localStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch {
        // 存不下也不该让登录流程崩掉，本次会话内仍可用内存中的身份。
    }
    listeners.forEach((listener) => listener(token));
}

export function onTokenChange(listener: TokenListener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

export interface RegisterPayload {
    username: string;
    password: string;
    email?: string;
    display_name?: string;
    role?: 'student' | 'teacher';
}

export const authApi = {
    register: async (payload: RegisterPayload) => {
        const response = await client.post<TokenResponse>('/auth/register', payload);
        return response.data;
    },

    login: async (username: string, password: string) => {
        const response = await client.post<TokenResponse>('/auth/login', { username, password });
        return response.data;
    },

    me: async () => {
        const response = await client.get<AuthUser>('/auth/me');
        return response.data;
    },

    logout: async () => {
        await client.post('/auth/logout');
    },
};
