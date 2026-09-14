/**
 * 玻璃材质的基础组件。所有玻璃元件都由它派生，保证质感一致。
 *
 * 指针位置写进 CSS 变量，由 `.glass--lit` 的伪元素画出跟随指针的镜面高光。
 * 用 CSS 变量而不是 React state：高光每帧都在变，走 state 会让整棵子树重渲染。
 */
import {
    forwardRef,
    useCallback,
    useRef,
    type CSSProperties,
    type ElementType,
    type PointerEvent as ReactPointerEvent,
    type ReactNode,
} from 'react';
import { cn } from '../../lib/utils';

export type GlassTier = 'thin' | 'regular' | 'thick';
export type GlassTint = 'none' | 'primary' | 'accent' | 'danger';
export type GlassRadius = 'sm' | 'md' | 'lg' | 'xl' | 'pill';

const RADIUS_VAR: Record<GlassRadius, string> = {
    sm: 'var(--glass-radius-sm)',
    md: 'var(--glass-radius-md)',
    lg: 'var(--glass-radius-lg)',
    xl: 'var(--glass-radius-xl)',
    pill: 'var(--glass-radius-pill)',
};

export interface GlassSurfaceOwnProps {
    tier?: GlassTier;
    tint?: GlassTint;
    radius?: GlassRadius;
    /** 边缘折射。大面板开着好看，密集的小元件上关掉更清爽。 */
    refract?: boolean;
    /** 跟随指针的镜面高光。 */
    lit?: boolean;
    /** 细颗粒噪点，去掉塑料感。 */
    grain?: boolean;
    /** 悬停浮起、按下压实。 */
    interactive?: boolean;
    as?: ElementType;
    className?: string;
    style?: CSSProperties;
    children?: ReactNode;
}

type GlassSurfaceProps = GlassSurfaceOwnProps &
    Omit<React.HTMLAttributes<HTMLElement>, keyof GlassSurfaceOwnProps>;

export const GlassSurface = forwardRef<HTMLElement, GlassSurfaceProps>(
    function GlassSurface(
        {
            tier = 'regular',
            tint = 'none',
            radius,
            refract = true,
            lit = true,
            grain = true,
            interactive = false,
            as: Component = 'div',
            className,
            style,
            children,
            onPointerMove,
            ...rest
        },
        forwardedRef,
    ) {
        const localRef = useRef<HTMLElement | null>(null);

        const setRef = useCallback(
            (node: HTMLElement | null) => {
                localRef.current = node;
                if (typeof forwardedRef === 'function') forwardedRef(node);
                else if (forwardedRef) forwardedRef.current = node;
            },
            [forwardedRef],
        );

        const handlePointerMove = useCallback(
            (event: ReactPointerEvent<HTMLElement>) => {
                onPointerMove?.(event);
                if (!lit) return;
                const node = localRef.current;
                if (!node) return;
                const rect = node.getBoundingClientRect();
                node.style.setProperty(
                    '--glass-pointer-x',
                    `${((event.clientX - rect.left) / rect.width) * 100}%`,
                );
                node.style.setProperty(
                    '--glass-pointer-y',
                    `${((event.clientY - rect.top) / rect.height) * 100}%`,
                );
            },
            [lit, onPointerMove],
        );

        const Tag = Component as ElementType;
        return (
            <Tag
                ref={setRef}
                className={cn(
                    'glass',
                    `glass--${tier}`,
                    refract && 'glass--refract',
                    lit && 'glass--lit',
                    grain && 'glass--grain',
                    interactive && 'glass--interactive',
                    tint !== 'none' && `glass--tint-${tint}`,
                    className,
                )}
                style={{
                    ...(radius ? { borderRadius: RADIUS_VAR[radius] } : null),
                    ...style,
                }}
                onPointerMove={handlePointerMove}
                {...rest}
            >
                {children}
            </Tag>
        );
    },
);
