import { api } from '../../api/client';
import type { GraphData, GraphJob, GraphJobEvent } from '../../types';

export interface JobProgress {
  step: string;
  message: string;
}

/** 轮询间隔。1.2 秒够"看得出在动"，又不至于把服务打爆。 */
const POLL_MS = 1200;

/**
 * 开一条星图生成任务并等它出结果，对调用方来说和以前那次 await 一模一样。
 *
 * 为什么不再用 SSE：那是一条几分钟里几乎不发字节的长连接。模型静默期一到，
 * 任何一层有读超时的中间件都会把它掐掉 —— 浏览器只说一句 network error，而
 * 后端那个线程还在照常跑完，日志里一条错都没有，根本没法查。而且刷新一下
 * 页面，连接就没了，生成还在服务端跑着但界面上什么都不剩。
 *
 * 任务模式下这两件事都不成立：请求立刻返回，之后全是短轮询；即使这个 await
 * 被用户刷新掉，任务仍然在服务端跑，生成任务面板还能把它接回来。
 */
export async function runGraphJob(
  create: () => Promise<GraphJob>,
  onProgress: (p: JobProgress) => void,
  signal?: AbortSignal,
): Promise<GraphData> {
  const started = await create();
  let since = started.last_seq;
  reportProgress(started.events, onProgress);

  if (started.status === 'done' && started.graph) return started.graph;
  if (started.status === 'failed') throw new Error(started.error || '星图生成失败');

  for (;;) {
    await sleep(POLL_MS, signal);
    // AbortSignal 只是让这个 await 停下来；任务本身继续在服务端跑，面板里还
    // 看得到 —— 这是有意的，用户切走不代表要把生成扔掉。
    if (signal?.aborted) throw new DOMException('已取消等待', 'AbortError');

    const job = await api.getGraphJob(started.job_id, since);
    since = Math.max(since, job.last_seq);
    reportProgress(job.events, onProgress);

    if (job.status === 'done') {
      if (job.graph) return job.graph;
      // 理论上不会发生：done 必然带图。真碰上了就明说，别返回一个空图。
      throw new Error('任务已完成但没有拿到星图数据');
    }
    if (job.status === 'failed') throw new Error(job.error || '星图生成失败');
  }
}

/** 只把阶段标签报给进度条 —— reasoning / content 增量归生成任务面板显示。 */
function reportProgress(
  events: GraphJobEvent[],
  onProgress: (p: JobProgress) => void,
): void {
  for (const event of events) {
    if (event.kind === 'progress') {
      onProgress({ step: event.step, message: event.text });
    }
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}
