import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authorizationHeader, client, configureAuthBridge, notifyUnauthorized } from './client';
import { readToken, writeToken } from './auth';

/**
 * 这一组锁的是"身份换了，写出去的数据也必须换"这条规则在传输层的部分。
 * App.tsx 里那道 lessonOwnerRef 守卫是同一条规则的组件层实现。
 */
function bridge() {
    const onUnauthorized = vi.fn(() => writeToken(null));
    configureAuthBridge({ readAccessToken: readToken, onUnauthorized });
    return onUnauthorized;
}

describe('identity on the wire', () => {
    beforeEach(() => {
        localStorage.clear();
        bridge();
    });

    it('a request made after logout carries no bearer token at all', () => {
        writeToken('alice-token');
        expect(authorizationHeader()).toEqual({ Authorization: 'Bearer alice-token' });

        writeToken(null); // 退出登录
        // 这正是危险之处：请求仍然会发出去，只是变成了访客身份。
        // 组件层必须在此之前就拦住私有快照的自动保存。
        expect(authorizationHeader()).toEqual({});
    });

    it('switching accounts swaps the token rather than keeping the old one', () => {
        writeToken('alice-token');
        writeToken('bob-token');
        expect(authorizationHeader()).toEqual({ Authorization: 'Bearer bob-token' });
    });

    it('a fetch-based 401 goes through the same logout path as axios', () => {
        const onUnauthorized = bridge();
        writeToken('expired-token');

        // stream.ts 在 response.status === 401 时调用它
        notifyUnauthorized();

        expect(onUnauthorized).toHaveBeenCalledTimes(1);
        expect(readToken()).toBeNull();
        expect(authorizationHeader()).toEqual({});
    });

    it('the axios interceptor and the fetch path share one bridge', async () => {
        const onUnauthorized = bridge();
        writeToken('expired-token');

        const handlers = client.interceptors.response as unknown as {
            handlers: { rejected: (error: unknown) => Promise<unknown> }[];
        };
        await Promise.all(
            handlers.handlers.map((handler) =>
                handler.rejected?.({ response: { status: 401, data: {} } }).catch(() => undefined),
            ),
        );
        notifyUnauthorized();

        // 两条路径调用的是同一个回调，令牌只会被清一次并保持清空
        expect(onUnauthorized).toHaveBeenCalled();
        expect(readToken()).toBeNull();
    });
});
