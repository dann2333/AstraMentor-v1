/**
 * 玻璃按钮。
 *
 * 三层反馈：悬停浮起（Motion 弹簧）、按下压实、以及按下位置扩散的涟漪。
 * 涟漪用 Motion 的 AnimatePresence 管理，快速连点时旧涟漪会自然淡出，
 * 而不是被硬生生截断。
 */
import {
    forwardRef,
    useCallback,
    useRef,
    useState,
    type ButtonHTMLAttributes,
    type MouseEvent as ReactMouseEvent,
} from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { cn } from '../../lib/utils';
import { springSnappy } from '../../lib/motion';
import type { GlassTier, GlassTint } from './GlassSurface';

type Size = 'sm' | 'md' | 'lg' | 'icon';

const SIZE_CLASS: Record<Size, string> = {
    sm: 'h-8 px-3 text-xs gap-1.5',
    md: 'h-10 px-4 text-sm gap-2',
    lg: 'h-12 px-6 text-base gap-2.5',
    icon: 'h-10 w-10 p-0',
};

/**
 * Motion 把 onDrag / onAnimationStart 等事件名重定义成了自己的手势回调，
 * 与 React 原生的同名 DOM 事件签名冲突。这里把冲突的几个摘掉 ——
 * 玻璃按钮本来也不需要原生拖拽事件。
 */
type NativeButtonProps = Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    | 'onDrag'
    | 'onDragStart'
    | 'onDragEnd'
    | 'onDragEnter'
    | 'onDragLeave'
    | 'onDragOver'
    | 'onDrop'
    | 'onAnimationStart'
    | 'onAnimationEnd'
    | 'onAnimationIteration'
    | 'style'
>;

export interface GlassButtonProps extends NativeButtonProps {
    tint?: GlassTint;
    tier?: GlassTier;
    size?: Size;
    /** 无边框的纯文字按钮，用于次级操作。 */
    plain?: boolean;
    pill?: boolean;
}

interface Ripple {
    id: number;
    x: number;
    y: number;
}

export const GlassButton = forwardRef<HTMLButtonElement, GlassButtonProps>(
    function GlassButton(
        {
            tint = 'none',
            tier = 'thin',
            size = 'md',
            plain = false,
            pill = false,
            className,
            children,
            onPointerMove,
            onClick,
            disabled,
            ...rest
        },
        forwardedRef,
    ) {
        const localRef = useRef<HTMLButtonElement | null>(null);
        const [ripples, setRipples] = useState<Ripple[]>([]);
        const rippleId = useRef(0);

        const setRef = useCallback(
            (node: HTMLButtonElement | null) => {
                localRef.current = node;
                if (typeof forwardedRef === 'function') forwardedRef(node);
                else if (forwardedRef) forwardedRef.current = node;
            },
            [forwardedRef],
        );

        const handlePointerMove = useCallback(
            (event: React.PointerEvent<HTMLButtonElement>) => {
                onPointerMove?.(event);
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
            [onPointerMove],
        );

        const handleClick = useCallback(
            (event: ReactMouseEvent<HTMLButtonElement>) => {
                const node = localRef.current;
                if (node) {
                    const rect = node.getBoundingClientRect();
                    const id = ++rippleId.current;
                    setRipples((current) => [
                        ...current,
                        { id, x: event.clientX - rect.left, y: event.clientY - rect.top },
                    ]);
                    // 涟漪播完自己退场；不清理会一直堆在 DOM 里。
                    window.setTimeout(
                        () => setRipples((current) => current.filter((r) => r.id !== id)),
                        620,
                    );
                }
                onClick?.(event);
            },
            [onClick],
        );

        return (
            <motion.button
                ref={setRef}
                type="button"
                disabled={disabled}
                className={cn(
                    'relative inline-flex select-none items-center justify-center overflow-hidden',
                    'font-medium tracking-tight whitespace-nowrap',
                    'disabled:pointer-events-none disabled:opacity-45',
                    SIZE_CLASS[size],
                    plain
                        ? 'rounded-[var(--glass-radius-sm)] text-foreground/80 hover:text-foreground hover:bg-white/8'
                        : cn(
                              'glass glass--grain glass--lit glass--refract',
                              `glass--${tier}`,
                              tint !== 'none' && `glass--tint-${tint}`,
                          ),
                    !plain && pill && 'rounded-[var(--glass-radius-pill)]',
                    className,
                )}
                whileHover={disabled ? undefined : { y: -2, scale: 1.02 }}
                whileTap={disabled ? undefined : { y: 0, scale: 0.965 }}
                transition={springSnappy}
                onPointerMove={handlePointerMove}
                onClick={handleClick}
                {...rest}
            >
                <AnimatePresence>
                    {ripples.map((ripple) => (
                        <motion.span
                            key={ripple.id}
                            className="pointer-events-none absolute rounded-full bg-white/30"
                            style={{ left: ripple.x, top: ripple.y }}
                            initial={{ width: 0, height: 0, x: 0, y: 0, opacity: 0.55 }}
                            animate={{
                                width: 220,
                                height: 220,
                                x: -110,
                                y: -110,
                                opacity: 0,
                            }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                        />
                    ))}
                </AnimatePresence>
                <span className="relative z-[2] inline-flex items-center gap-[inherit]">
                    {children}
                </span>
            </motion.button>
        );
    },
);
