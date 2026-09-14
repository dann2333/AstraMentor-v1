import { useCallback } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

/**
 * 把指针在元素内的相对位置写进 CSS 变量，供 `.glass--lit` 画出跟随指针的
 * 镜面高光。
 *
 * 走 CSS 变量而不是 React state：高光每一帧都在变，用 state 会让整棵子树
 * 跟着重渲染，而这里要改的只是一个颜色停靠点。
 */
export function useGlassPointer<T extends HTMLElement>() {
    return useCallback((event: ReactPointerEvent<T>) => {
        const node = event.currentTarget;
        const rect = node.getBoundingClientRect();
        node.style.setProperty(
            '--glass-pointer-x',
            `${((event.clientX - rect.left) / rect.width) * 100}%`,
        );
        node.style.setProperty(
            '--glass-pointer-y',
            `${((event.clientY - rect.top) / rect.height) * 100}%`,
        );
    }, []);
}
