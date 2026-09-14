import { CheckCircle2, Loader2, CircleDashed } from 'lucide-react';
import type { GraphProgress } from '../../api/stream';

interface GraphGenerationProgressProps {
  /** 后端 SSE 推送过来的阶段进度（按时间顺序） */
  progress: GraphProgress[];
  /** 面板标题，例如「正在生成知识星图」 */
  title: string;
}

/**
 * 星图生成的分步进度面板。
 *
 * 生成一张星图要联网检索 + 调大模型，约 1 分钟。这里把后端推送的真实
 * 阶段逐条列出，已完成的打勾、进行中的转圈，让用户清楚知道进行到哪一步，
 * 而不是面对一个干等的转圈。
 *
 * 进度总共只有 4~5 条，列表自然展开、不用内部滚动条，
 * 避免滚动动画在事件连发时被反复打断造成的闪烁。
 */
export function GraphGenerationProgress({ progress, title }: GraphGenerationProgressProps) {
  const lastIndex = progress.length - 1;

  return (
    <div className="w-[22rem] max-w-[85vw] rounded-xl border border-border/60 bg-card/95 p-5 shadow-xl backdrop-blur">
      <div className="mb-4 flex items-center gap-2">
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      </div>

      {progress.length === 0 ? (
        <p className="text-sm text-muted-foreground">正在连接服务器…</p>
      ) : (
        <ul className="space-y-2.5">
          {progress.map((item, i) => {
            const isCurrent = i === lastIndex;
            return (
              <li key={i} className="flex items-start gap-2.5">
                {isCurrent ? (
                  <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-primary" />
                ) : (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                )}
                <span
                  className={`text-sm leading-snug ${
                    isCurrent ? 'text-foreground' : 'text-muted-foreground'
                  }`}
                >
                  {item.message}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      <div className="mt-4 flex items-center gap-2 border-t border-border/50 pt-3 text-xs text-muted-foreground">
        <CircleDashed className="h-3.5 w-3.5" />
        AI 正在生成，通常需要 30 秒到 1 分钟，请稍候
      </div>
    </div>
  );
}

export default GraphGenerationProgress;
