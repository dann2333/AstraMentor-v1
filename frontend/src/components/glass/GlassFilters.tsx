/**
 * 全局只挂载一次的 SVG 滤镜定义。
 *
 * `feDisplacementMap` 需要一张"位移图"：它的红/绿通道被当作 x/y 偏移量。
 * 这里用一张径向渐变当位移图 —— 中心是中性灰（不位移），越靠边偏移越大，
 * 于是背后的画面只在玻璃边缘被挤压，正中间保持清晰。这正是一块有厚度的
 * 玻璃该有的样子，也是它和"整块高斯模糊"的根本区别。
 */
export function GlassFilters() {
    return (
        <svg
            aria-hidden="true"
            focusable="false"
            style={{ position: 'absolute', width: 0, height: 0, overflow: 'hidden' }}
        >
            <defs>
                {/* 位移图：中性灰 = 不位移；红多 = 向右，绿多 = 向下 */}
                <radialGradient id="glass-displacement-map" cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stopColor="#808080" />
                    <stop offset="62%" stopColor="#808080" />
                    <stop offset="82%" stopColor="#9a9a9a" />
                    <stop offset="100%" stopColor="#c8c8c8" />
                </radialGradient>

                <filter
                    id="glass-refraction"
                    x="-16%"
                    y="-16%"
                    width="132%"
                    height="132%"
                    colorInterpolationFilters="sRGB"
                >
                    {/* 一点点湍流让边缘不是完美的圆，看起来像手工玻璃而非塑料 */}
                    <feTurbulence
                        type="fractalNoise"
                        baseFrequency="0.008 0.012"
                        numOctaves="2"
                        seed="7"
                        result="noise"
                    />
                    <feGaussianBlur in="noise" stdDeviation="2" result="softNoise" />
                    <feDisplacementMap
                        in="SourceGraphic"
                        in2="softNoise"
                        scale="14"
                        xChannelSelector="R"
                        yChannelSelector="G"
                    />
                </filter>

                {/* 更强的一档，给模态这种大面积玻璃用 */}
                <filter
                    id="glass-refraction-strong"
                    x="-20%"
                    y="-20%"
                    width="140%"
                    height="140%"
                    colorInterpolationFilters="sRGB"
                >
                    <feTurbulence
                        type="fractalNoise"
                        baseFrequency="0.006 0.01"
                        numOctaves="2"
                        seed="13"
                        result="noise"
                    />
                    <feGaussianBlur in="noise" stdDeviation="3" result="softNoise" />
                    <feDisplacementMap
                        in="SourceGraphic"
                        in2="softNoise"
                        scale="24"
                        xChannelSelector="R"
                        yChannelSelector="G"
                    />
                </filter>
            </defs>
        </svg>
    );
}
