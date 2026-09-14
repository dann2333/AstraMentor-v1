/**
 * 背景粒子场。
 *
 * 设计取向是"克制"，因此刻意不做这几件事：
 *
 *   - 不做鼠标磁吸，不画连到光标的线。那是 particles.js 的玩具感，
 *     用久了很吵，也会把注意力从内容上拽走。
 *   - 不闪烁、不呼吸。粒子只是缓慢漂移。
 *   - 不上彩虹色。绝大多数粒子取前景色的低透明度，只有约七分之一取主色或
 *     强调色 —— 颜色是点缀，不是主题。
 *
 * 它真正的作用是给毛玻璃**一层可以折射的真实内容**：粒子从玻璃面板背后经过时，
 * 会被面板的 backdrop-filter 揉开、被边缘挤压。这是"科技感"的来源，
 * 也是为什么它必须是全局的一层，而不只铺在首页。
 *
 * 三层视差（远/中/近）用大小、透明度和跟随系数拉开纵深；指针移动时三层位移
 * 不同步，画面因此有厚度，但位移只有几个像素，不会让人晕。
 */
import { useEffect, useRef } from 'react';

interface Layer {
    /** 该层粒子占总数的比例 */
    share: number;
    radius: [number, number];
    alpha: [number, number];
    /** 漂移速度（像素/帧） */
    speed: number;
    /** 指针视差系数：越靠近观察者跟得越多 */
    parallax: number;
    /** 只有最近的一层画星座连线 */
    linked: boolean;
}

const LAYERS: Layer[] = [
    { share: 0.5, radius: [0.6, 1.1], alpha: [0.26, 0.46], speed: 0.012, parallax: 3, linked: false },
    { share: 0.32, radius: [1.0, 1.7], alpha: [0.34, 0.58], speed: 0.022, parallax: 7, linked: false },
    { share: 0.18, radius: [1.5, 2.4], alpha: [0.42, 0.72], speed: 0.034, parallax: 14, linked: true },
];

/** 每多少平方像素放一个粒子。数字越大越稀疏。 */
const AREA_PER_PARTICLE = 10000;
const MAX_PARTICLES = 160;

/** 星座连线的最大距离与最大透明度 —— 两者都刻意压得很低。 */
const LINK_DISTANCE = 108;
const LINK_ALPHA = 0.09;

interface Particle {
    x: number;
    y: number;
    vx: number;
    vy: number;
    radius: number;
    alpha: number;
    /** 指向 palette 的下标 */
    tone: number;
    layer: number;
}

/** 从 CSS 变量取色，因此深浅两套主题自动成立。 */
function readPalette(): string[] {
    const style = getComputedStyle(document.documentElement);
    const read = (name: string, fallback: string) =>
        style.getPropertyValue(name).trim() || fallback;
    return [
        read('--foreground', '43 78% 91%'),
        read('--foreground', '43 78% 91%'),
        read('--foreground', '43 78% 91%'),
        read('--foreground', '43 78% 91%'),
        read('--foreground', '43 78% 91%'),
        read('--foreground', '43 78% 91%'),
        read('--accent', '45 100% 70%'),
        read('--primary', '12 100% 69%'),
    ];
}

export function ParticleField() {
    const canvasRef = useRef<HTMLCanvasElement | null>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d', { alpha: true });
        if (!ctx) return;

        const reduceMotion = window.matchMedia(
            '(prefers-reduced-motion: reduce)',
        ).matches;

        let width = 0;
        let height = 0;
        let particles: Particle[] = [];
        let palette = readPalette();
        let frame = 0;
        let running = true;

        // 指针视差：target 是即时值，current 缓动跟随，避免生硬跳动。
        const pointer = { targetX: 0, targetY: 0, x: 0, y: 0 };

        const seed = () => {
            const total = Math.min(
                MAX_PARTICLES,
                Math.round((width * height) / AREA_PER_PARTICLE),
            );
            particles = [];
            LAYERS.forEach((layer, layerIndex) => {
                const count = Math.round(total * layer.share);
                for (let i = 0; i < count; i += 1) {
                    const angle = Math.random() * Math.PI * 2;
                    particles.push({
                        x: Math.random() * width,
                        y: Math.random() * height,
                        vx: Math.cos(angle) * layer.speed,
                        vy: Math.sin(angle) * layer.speed,
                        radius:
                            layer.radius[0] +
                            Math.random() * (layer.radius[1] - layer.radius[0]),
                        alpha:
                            layer.alpha[0] +
                            Math.random() * (layer.alpha[1] - layer.alpha[0]),
                        // 约七分之一取彩色，其余取前景色
                        tone: Math.floor(Math.random() * palette.length),
                        layer: layerIndex,
                    });
                }
            });
        };

        const resize = () => {
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            width = window.innerWidth;
            height = window.innerHeight;
            canvas.width = Math.round(width * dpr);
            canvas.height = Math.round(height * dpr);
            canvas.style.width = `${width}px`;
            canvas.style.height = `${height}px`;
            // setTransform 而不是 scale：scale 是累乘的，每次 resize 都会叠加一次。
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            seed();
        };

        const draw = () => {
            ctx.clearRect(0, 0, width, height);

            pointer.x += (pointer.targetX - pointer.x) * 0.045;
            pointer.y += (pointer.targetY - pointer.y) * 0.045;

            // 先画连线，再画粒子，这样粒子压在线上而不是被线切开
            const near = particles.filter((p) => LAYERS[p.layer].linked);
            const shift = LAYERS[LAYERS.length - 1].parallax;
            for (let i = 0; i < near.length; i += 1) {
                const a = near[i];
                for (let j = i + 1; j < near.length; j += 1) {
                    const b = near[j];
                    const dx = a.x - b.x;
                    const dy = a.y - b.y;
                    const distance = Math.hypot(dx, dy);
                    if (distance >= LINK_DISTANCE) continue;
                    ctx.beginPath();
                    ctx.strokeStyle = `hsl(${palette[0]} / ${(
                        LINK_ALPHA *
                        (1 - distance / LINK_DISTANCE)
                    ).toFixed(4)})`;
                    ctx.lineWidth = 0.6;
                    ctx.moveTo(a.x + pointer.x * shift, a.y + pointer.y * shift);
                    ctx.lineTo(b.x + pointer.x * shift, b.y + pointer.y * shift);
                    ctx.stroke();
                }
            }

            for (const p of particles) {
                const layer = LAYERS[p.layer];
                if (!reduceMotion) {
                    p.x += p.vx;
                    p.y += p.vy;
                    if (p.x < -4) p.x = width + 4;
                    else if (p.x > width + 4) p.x = -4;
                    if (p.y < -4) p.y = height + 4;
                    else if (p.y > height + 4) p.y = -4;
                }
                ctx.beginPath();
                ctx.arc(
                    p.x + pointer.x * layer.parallax,
                    p.y + pointer.y * layer.parallax,
                    p.radius,
                    0,
                    Math.PI * 2,
                );
                ctx.fillStyle = `hsl(${palette[p.tone]} / ${p.alpha})`;
                ctx.fill();
            }
        };

        const loop = () => {
            if (!running) return;
            draw();
            frame = requestAnimationFrame(loop);
        };

        const onPointerMove = (event: PointerEvent) => {
            // 归一到 [-1, 1]，再乘以每层的视差系数
            pointer.targetX = (event.clientX / window.innerWidth) * 2 - 1;
            pointer.targetY = (event.clientY / window.innerHeight) * 2 - 1;
        };

        // 标签页不可见时停掉，别在后台空转烧电
        const onVisibility = () => {
            if (document.hidden) {
                running = false;
                cancelAnimationFrame(frame);
            } else if (!running) {
                running = true;
                frame = requestAnimationFrame(loop);
            }
        };

        // 切换主题时重新取色
        const themeObserver = new MutationObserver(() => {
            palette = readPalette();
        });
        themeObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['class'],
        });

        resize();
        if (reduceMotion) {
            draw();
        } else {
            frame = requestAnimationFrame(loop);
            window.addEventListener('pointermove', onPointerMove, { passive: true });
        }
        window.addEventListener('resize', resize);
        document.addEventListener('visibilitychange', onVisibility);

        return () => {
            running = false;
            cancelAnimationFrame(frame);
            themeObserver.disconnect();
            window.removeEventListener('resize', resize);
            window.removeEventListener('pointermove', onPointerMove);
            document.removeEventListener('visibilitychange', onVisibility);
        };
    }, []);

    return (
        <canvas
            ref={canvasRef}
            aria-hidden="true"
            // pointer-events: none 是硬要求 —— 这是一层铺满视口的画布，
            // 一旦能接收事件，它会吃掉底下所有内容的点击。
            className="pointer-events-none fixed inset-0 z-0"
        />
    );
}
