import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { GraphJob, GraphJobEvent } from '../../types';

/** 单条任务在前端的形态：服务端快照 + 本地累积的事件流。 */
export interface TrackedJob extends GraphJob {
  /** 从第 1 条起累积的完整事件流（服务端只回增量） */
  log: GraphJobEvent[];
}

/** 有任务在跑时的轮询间隔。1.2 秒够"看得出在动"，又不至于把服务打爆。 */
const ACTIVE_POLL_MS = 1200;
/** 都跑完之后仍然低频对一下，好接住别的标签页开的新任务 */
const IDLE_POLL_MS = 15000;
/** 每条任务在前端最多留多少条事件，防止长任务把内存吃掉 */
const MAX_LOG = 400;

function mergeLog(previous: GraphJobEvent[], incoming: GraphJobEvent[]): GraphJobEvent[] {
  if (!incoming.length) return previous;
  // 服务端会把连续的同类增量并进上一条，所以同一个 seq 可能带着更长的文本
  // 再回来一次 —— 按 seq 覆盖，不能直接 concat，否则会出现重复片段。
  const bySeq = new Map(previous.map((e) => [e.seq, e]));
  for (const event of incoming) bySeq.set(event.seq, event);
  const merged = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
  return merged.length > MAX_LOG ? merged.slice(merged.length - MAX_LOG) : merged;
}

/**
 * 跟踪服务端的星图生成任务。
 *
 * 为什么要有：生成原来是一条 SSE 长连接，刷新一下就什么都不剩，中途模型静默
 * 几分钟还会被反代的读超时掐断。现在生成跑在服务端的任务里，这个 hook 负责
 * 把它们拉回来 —— 刷新、切标签、换设备都能接着看，包括模型的实时输出。
 */
export function useGraphJobs(enabled: boolean) {
  const [jobs, setJobs] = useState<TrackedJob[]>([]);
  // 事件流放 ref：轮询回调里要读最新值，又不想因为它变化而重建 effect。
  const logsRef = useRef<Map<string, GraphJobEvent[]>>(new Map());
  const timerRef = useRef<number | null>(null);
  const stoppedRef = useRef(false);

  const refresh = useCallback(async (): Promise<boolean> => {
    const summaries = await api.listGraphJobs();

    // 只对"还在跑"和"刚完成但本地还没拿到星图"的任务拉增量。已经看完的
    // 历史任务不必每轮都去问一遍。
    const detailed = await Promise.all(
      summaries.map(async (summary) => {
        const known = logsRef.current.get(summary.job_id) ?? [];
        const since = known.length ? known[known.length - 1].seq : 0;
        if (summary.status !== 'running' && since >= summary.last_seq) {
          return { ...summary, log: known } as TrackedJob;
        }
        try {
          const full = await api.getGraphJob(summary.job_id, since);
          const log = mergeLog(known, full.events);
          logsRef.current.set(summary.job_id, log);
          return { ...full, log } as TrackedJob;
        } catch {
          // 单条任务拉不到（刚被别处关掉）不该让整轮刷新失败
          return { ...summary, log: known } as TrackedJob;
        }
      }),
    );

    // 已经消失的任务顺手把本地事件流清掉，别让 Map 无限长
    const alive = new Set(detailed.map((j) => j.job_id));
    for (const id of [...logsRef.current.keys()]) {
      if (!alive.has(id)) logsRef.current.delete(id);
    }

    setJobs(detailed);
    return detailed.some((job) => job.status === 'running');
  }, []);

  useEffect(() => {
    stoppedRef.current = false;
    if (!enabled) {
      setJobs([]);
      logsRef.current.clear();
      return;
    }

    const tick = async () => {
      let hasActive = false;
      try {
        hasActive = await refresh();
      } catch {
        // 网络抖动不该让轮询停掉，下一轮继续
      }
      if (stoppedRef.current) return;
      timerRef.current = window.setTimeout(
        tick,
        hasActive ? ACTIVE_POLL_MS : IDLE_POLL_MS,
      );
    };
    void tick();

    return () => {
      stoppedRef.current = true;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [enabled, refresh]);

  const dismiss = useCallback(
    async (jobId: string) => {
      // 先从界面上去掉，别让用户点完还要等一个来回
      setJobs((prev) => prev.filter((job) => job.job_id !== jobId));
      logsRef.current.delete(jobId);
      try {
        await api.dismissGraphJob(jobId);
      } catch {
        // 已经不存在也算达到目的
      }
    },
    [],
  );

  /** 取一条任务的完整快照（含星图），用于"打开"。 */
  const load = useCallback(async (jobId: string) => api.getGraphJob(jobId, 0), []);

  return { jobs, refresh, dismiss, load };
}
