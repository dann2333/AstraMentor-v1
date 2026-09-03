import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authorizationHeader, client, configureAuthBridge } from './client';
import { onTokenChange, readToken, writeToken } from './auth';
import { ApiRequestError } from './errors';

/** 复现 AuthContext 顶层那次注入，让本文件可以独立运行。 */
function bridge() {
    const onUnauthorized = vi.fn(() => writeToken(null));
    configureAuthBridge({ readAccessToken: readToken, onUnauthorized });
    return onUnauthorized;
}

describe('token storage', () => {
    beforeEach(() => {
        localStorage.clear();
        bridge();
    });

    it('round-trips the token and clears it', () => {
        expect(readToken()).toBeNull();
        writeToken('abc123');
        expect(readToken()).toBe('abc123');
        writeToken(null);
        expect(readToken()).toBeNull();
    });

    it('notifies listeners so the UI can drop the signed-in state', () => {
        const seen: (string | null)[] = [];
        const unsubscribe = onTokenChange((token) => seen.push(token));
        writeToken('abc123');
        writeToken(null);
        unsubscribe();
        writeToken('ignored');
        expect(seen).toEqual(['abc123', null]);
    });

    it('survives a localStorage that throws (private browsing)', () => {
        const getItem = vi
            .spyOn(Storage.prototype, 'getItem')
            .mockImplementation(() => {
                throw new Error('denied');
            });
        const setItem = vi
            .spyOn(Storage.prototype, 'setItem')
            .mockImplementation(() => {
                throw new Error('denied');
            });
        expect(readToken()).toBeNull();
        expect(() => writeToken('abc123')).not.toThrow();
        getItem.mockRestore();
        setItem.mockRestore();
    });
});

describe('authorization header', () => {
    beforeEach(() => {
        localStorage.clear();
        bridge();
    });

    it('is empty for guests and carries the bearer token once signed in', () => {
        expect(authorizationHeader()).toEqual({});
        writeToken('abc123');
        expect(authorizationHeader()).toEqual({ Authorization: 'Bearer abc123' });
    });

    it('is attached by the request interceptor', async () => {
        writeToken('abc123');
        const handlers = client.interceptors.request as unknown as {
            handlers: { fulfilled: (config: unknown) => unknown }[];
        };
        const headers = new Map<string, string>();
        const config = { headers: { set: (key: string, value: string) => headers.set(key, value) } };
        handlers.handlers.forEach((handler) => handler.fulfilled?.(config));
        expect(headers.get('Authorization')).toBe('Bearer abc123');
    });
});

describe('401 handling', () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it('clears the stored token so the app cannot keep writing as a stale identity', async () => {
        const onUnauthorized = bridge();
        writeToken('expired-token');

        const handlers = client.interceptors.response as unknown as {
            handlers: { rejected: (error: unknown) => Promise<unknown> }[];
        };
        const failure = { response: { status: 401, data: { detail: 'token has expired' } } };
        await Promise.all(
            handlers.handlers.map((handler) =>
                handler.rejected?.(failure).catch((error: unknown) => {
                    expect(error).toBeInstanceOf(ApiRequestError);
                }),
            ),
        );

        expect(onUnauthorized).toHaveBeenCalled();
        expect(readToken()).toBeNull();
    });

    it('leaves the token alone for other failures', async () => {
        const onUnauthorized = bridge();
        writeToken('still-good');

        const handlers = client.interceptors.response as unknown as {
            handlers: { rejected: (error: unknown) => Promise<unknown> }[];
        };
        const failure = { response: { status: 500, data: { detail: 'boom' } } };
        await Promise.all(
            handlers.handlers.map((handler) => handler.rejected?.(failure).catch(() => undefined)),
        );

        expect(onUnauthorized).not.toHaveBeenCalled();
        expect(readToken()).toBe('still-good');
    });
});
