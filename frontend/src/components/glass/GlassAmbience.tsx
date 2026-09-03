/**
 * 背景氛围光。
 *
 * 毛玻璃需要背后有东西可透 —— 背景是一块纯色时，再精细的材质也看不出来。
 * 这里放几团缓慢漂移的色光，玻璃面板经过时会把它们晕开、折射，材质才成立。
 *
 * 光斑本身不参与交互，也不该抢注意力：动得很慢，透明度很低，
 * 并且在"减少动态效果"下完全静止。
 */
import { useMemo } from 'react';
import { motion, useReducedMotion } from 'motion/react';

interface Orb {
    color: string;
    size: number;
    top: string;
    left: string;
    duration: number;
    delay: number;
    drift: [number, number];
}

const ORBS: Orb[] = [
    {
        color: 'hsl(var(--primary))',
        size: 520,
        top: '-12%',
        left: '-8%',
        duration: 34,
        delay: 0,
        drift: [90, 60],
    },
    {
        color: 'hsl(var(--accent))',
        size: 440,
        top: '48%',
        left: '62%',
        duration: 42,
        delay: 2,
        drift: [-70, -50],
    },
    {
        color: 'hsl(var(--ring))',
        size: 380,
        top: '68%',
        left: '6%',
        duration: 38,
        delay: 4,
        drift: [60, -70],
    },
    {
        color: 'hsl(var(--secondary))',
        size: 460,
        top: '4%',
        left: '58%',
        duration: 46,
        delay: 1,
        drift: [-80, 70],
    },
];

export function GlassAmbience() {
    const reduceMotion = useReducedMotion();
    const orbs = useMemo(() => ORBS, []);

    return (
        <div className="glass-ambience" aria-hidden="true">
            {orbs.map((orb, index) => (
                <motion.span
                    key={index}
                    className="glass-ambience__orb"
                    style={{
                        width: orb.size,
                        height: orb.size,
                        top: orb.top,
                        left: orb.left,
                        background: `radial-gradient(circle at 35% 30%, ${orb.color}, transparent 68%)`,
                    }}
                    animate={
                        reduceMotion
                            ? undefined
                            : {
                                  x: [0, orb.drift[0], 0],
                                  y: [0, orb.drift[1], 0],
                                  scale: [1, 1.12, 1],
                              }
                    }
                    transition={{
                        duration: orb.duration,
                        delay: orb.delay,
                        repeat: Infinity,
                        ease: 'easeInOut',
                    }}
                />
            ))}
        </div>
    );
}
