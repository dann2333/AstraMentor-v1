/**
 * 全站共用的动效参数。
 *
 * iOS 的动画几乎不用固定时长的缓动曲线，用的是弹簧：位移由质量、劲度和阻尼
 * 决定，因此被打断时能从当前速度接着跑，而不是跳回去重来。这里把几档弹簧
 * 固定下来，避免每个组件各调一套参数、整体看起来不像同一个系统。
 */
import type { Transition, Variants } from 'motion/react';

/** 轻量元件：按钮、标签、图标。快，但仍有一点回弹。 */
export const springSnappy: Transition = {
    type: 'spring',
    stiffness: 520,
    damping: 32,
    mass: 0.7,
};

/** 面板与卡片：默认档。 */
export const springSmooth: Transition = {
    type: 'spring',
    stiffness: 320,
    damping: 34,
    mass: 0.9,
};

/** 模态与大面积位移：慢一点，重一点，读得清楚。 */
export const springHeavy: Transition = {
    type: 'spring',
    stiffness: 220,
    damping: 30,
    mass: 1.1,
};

/** 纯透明度过渡不该用弹簧——弹簧会让它闪一下。 */
export const fade: Transition = { duration: 0.24, ease: [0.16, 1, 0.3, 1] };

/** 模态：从稍小、稍下方浮起来，同时玻璃变清晰。 */
export const modalVariants: Variants = {
    hidden: { opacity: 0, scale: 0.94, y: 18, filter: 'blur(6px)' },
    visible: {
        opacity: 1,
        scale: 1,
        y: 0,
        filter: 'blur(0px)',
        transition: springHeavy,
    },
    exit: {
        opacity: 0,
        scale: 0.97,
        y: 8,
        filter: 'blur(4px)',
        transition: { duration: 0.18, ease: [0.4, 0, 1, 1] },
    },
};

/** 遮罩：只做透明度，避免和模态的弹簧打架。 */
export const overlayVariants: Variants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: fade },
    exit: { opacity: 0, transition: { duration: 0.16 } },
};

/** 列表容器：子项依次入场，而不是整块一起出现。 */
export const listVariants: Variants = {
    hidden: {},
    visible: {
        transition: { staggerChildren: 0.045, delayChildren: 0.04 },
    },
};

export const listItemVariants: Variants = {
    hidden: { opacity: 0, y: 14, scale: 0.98 },
    visible: { opacity: 1, y: 0, scale: 1, transition: springSmooth },
    exit: { opacity: 0, y: -8, scale: 0.98, transition: { duration: 0.16 } },
};

/** 页面切换：新页面从下方浮入，旧页面向上淡出。 */
export const pageVariants: Variants = {
    hidden: { opacity: 0, y: 24, filter: 'blur(8px)' },
    visible: {
        opacity: 1,
        y: 0,
        filter: 'blur(0px)',
        transition: { ...springHeavy, staggerChildren: 0.05 },
    },
    exit: {
        opacity: 0,
        y: -16,
        filter: 'blur(6px)',
        transition: { duration: 0.22, ease: [0.4, 0, 1, 1] },
    },
};

/** 可交互元件的悬停/按下手势。 */
export const pressable = {
    whileHover: { y: -2, scale: 1.015 },
    whileTap: { y: 0, scale: 0.975 },
    transition: springSnappy,
} as const;

/** 用于 <motion.div layout> 的布局过渡。 */
export const layoutTransition: Transition = springSmooth;
