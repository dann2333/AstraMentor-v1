/**
 * 背景。
 *
 * 这里原本是四团缓慢漂移的彩色光斑。去掉了 —— 那是"AI 生成落地页"最典型的
 * 签名，而且它其实是在替玻璃作弊：真正的毛玻璃应该透出**页面本身的内容**
 * （星空、课程卡、星图），而不是几团专门摆在那儿给它折射的装饰色块。
 *
 * 留下的是一层极淡、静止的光照梯度：只负责给画面定一个光源方向（左上偏亮、
 * 右下偏暗），让所有玻璃元件的左上高光在物理上说得通。它不动，也不抢注意力。
 */
import { useReducedMotion } from 'motion/react';

export function GlassAmbience() {
    // 保留这个 hook 是为了将来若加入动效时行为一致；当前这层本就是静止的。
    useReducedMotion();

    return (
        <div className="glass-ambience" aria-hidden="true">
            <span className="glass-ambience__key" />
            <span className="glass-ambience__fill" />
        </div>
    );
}
